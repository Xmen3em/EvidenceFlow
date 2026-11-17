import hmac
from hashlib import sha256
from pathlib import PurePath
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db import Actor, Database, DomainError, audit, get_case
from app.extract import ExtractionError, field_value, preview_page
from app.rules import LABELS, review
from app.settings import MAX_DOCUMENTS, MAX_UPLOAD

DOCUMENT_COLUMNS = "id, filename, kind, status, media_type, facts, error, created_at"


class Evidence:
    def __init__(self, db: Database, objects):
        self.db = db
        self.objects = objects

    def list_cases(self, actor: Actor):
        with self.db.connect() as conn:
            return conn.execute(
                """SELECT c.id,c.title,c.reference,c.revision,c.created_at,
                   (SELECT count(*) FROM documents d WHERE d.workspace_id=c.workspace_id AND d.case_id=c.id)
                   AS document_count
                FROM cases c WHERE c.workspace_id=%s ORDER BY c.created_at DESC LIMIT 100""",
                (actor.workspace_id,),
            ).fetchall()

    def create_case(self, actor: Actor, title: str, reference: str, *, case_id: UUID | None = None):
        with self.db.connect() as conn:
            key = case_id or uuid4()
            row = conn.execute(
                """INSERT INTO cases (id,workspace_id,title,reference) VALUES (%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING RETURNING id""",
                (key, actor.workspace_id, title, reference),
            ).fetchone()
            case = get_case(conn, actor, key)
            if row:
                audit(conn, actor, "case.created", key)
            return case

    def snapshot(self, actor: Actor, case_id: UUID, *, event: str | None = None):
        with self.db.connect() as conn:
            case = get_case(conn, actor, case_id, lock=True)
            docs = conn.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE workspace_id=%s AND case_id=%s ORDER BY created_at,id",
                (actor.workspace_id, case_id),
            ).fetchall()
            corrected = conn.execute(
                """SELECT DISTINCT ON (document_id,field_index) document_id,field_index,value
                FROM field_corrections WHERE workspace_id=%s AND case_id=%s
                ORDER BY document_id,field_index,id DESC""",
                (actor.workspace_id, case_id),
            ).fetchall()
            by_id = {doc["id"]: doc for doc in docs}
            for correction in corrected:
                fact = by_id[correction["document_id"]]["facts"]["fields"][correction["field_index"]]
                fact["extracted_value"] = fact["value"]
                fact["value"] = correction["value"]
            findings = review(case, docs)
            proposals = conn.execute(
                """SELECT p.id,p.revision,p.decision,p.note,p.status,p.created_at,p.decided_at,
                   m.name AS proposed_by, r.name AS decided_by, p.proposed_by=%s AS is_mine
                FROM proposals p JOIN members m ON m.workspace_id=p.workspace_id AND m.id=p.proposed_by
                LEFT JOIN members r ON r.workspace_id=p.workspace_id AND r.id=p.decided_by
                WHERE p.workspace_id=%s AND p.case_id=%s ORDER BY p.created_at DESC LIMIT 50""",
                (actor.user_id, actor.workspace_id, case_id),
            ).fetchall()
            if event:
                audit(conn, actor, event, case_id, revision=case["revision"])
            return {"case": case, "documents": docs, "review": findings, "proposals": proposals}

    def activity(self, actor: Actor, case_id: UUID):
        with self.db.connect() as conn:
            get_case(conn, actor, case_id)
            return conn.execute(
                """SELECT a.event, a.metadata, a.created_at, m.name AS actor FROM audit_events a
                LEFT JOIN members m ON m.workspace_id=a.workspace_id AND m.id=a.user_id
                WHERE a.workspace_id=%s AND a.case_id=%s ORDER BY a.id DESC LIMIT 100""",
                (actor.workspace_id, case_id),
            ).fetchall()

    def upload(self, actor: Actor, case_id: UUID, kind: str, filename: str, content: bytes):
        from app.storage import object_key

        if kind not in LABELS:
            raise DomainError(422, "Choose purchase order, invoice, or goods receipt.")
        if not content or len(content) > MAX_UPLOAD:
            raise DomainError(413, "Choose a nonempty file of up to 5 MiB.")
        filename = PurePath(filename.replace("\\", "/")).name.strip()
        if not filename or not filename.isprintable() or len(filename) > 120:
            raise DomainError(422, "Use a printable filename of up to 120 characters.")
        suffix = PurePath(filename).suffix.lower()
        if suffix == ".pdf" and content.startswith(b"%PDF-"):
            media_type = "application/pdf"
        elif suffix == ".txt" and b"\x00" not in content:
            media_type = "text/plain"
        else:
            raise DomainError(415, "Upload a PDF or a UTF-8 .txt file.")
        document_id, digest = uuid4(), sha256(content).hexdigest()
        with self.db.connect() as conn:
            get_case(conn, actor, case_id, lock=True)
            existing = conn.execute(
                "SELECT digest FROM documents WHERE workspace_id=%s AND case_id=%s",
                (actor.workspace_id, case_id),
            ).fetchall()
            if any(row["digest"] == digest for row in existing):
                raise DomainError(409, "This exact file is already in the case.")
            if len(existing) >= MAX_DOCUMENTS:
                raise DomainError(409, "This case already has 20 documents.")
            self.objects.put(object_key(actor.workspace_id, case_id, document_id), content)
            conn.execute(
                """INSERT INTO documents (id,workspace_id,case_id,uploaded_by,kind,filename,media_type,digest)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    document_id,
                    actor.workspace_id,
                    case_id,
                    actor.user_id,
                    kind,
                    filename,
                    media_type,
                    digest,
                ),
            )
            conn.execute("UPDATE cases SET revision=revision+1 WHERE id=%s", (case_id,))
            audit(conn, actor, "document.queued", case_id, document_id=str(document_id), kind=kind)
        return {"id": document_id, "status": "queued"}

    def download(self, actor: Actor, case_id: UUID, document_id: UUID):
        from app.storage import object_key

        with self.db.connect() as conn:
            get_case(conn, actor, case_id)
            doc = conn.execute(
                "SELECT filename,digest FROM documents WHERE workspace_id=%s AND case_id=%s AND id=%s",
                (actor.workspace_id, case_id, document_id),
            ).fetchone()
            if not doc:
                raise DomainError(404, "Document not found.")
        content = self.objects.get(object_key(actor.workspace_id, case_id, document_id))
        if not hmac.compare_digest(sha256(content).hexdigest(), doc["digest"]):
            raise DomainError(503, "Stored document failed its integrity check.")
        return doc["filename"], content

    def document_page(self, actor: Actor, case_id: UUID, document_id: UUID, page: int):
        filename, content = self.download(actor, case_id, document_id)
        if not filename.lower().endswith(".pdf"):
            raise DomainError(404, "Page previews are available for PDFs.")
        try:
            return preview_page(content, page)
        except ExtractionError as exc:
            raise DomainError(422, str(exc)) from None

    def correct_field(
        self,
        actor: Actor,
        case_id: UUID,
        document_id: UUID,
        index: int,
        revision: int,
        value: str,
        reason: str,
    ):
        if actor.role != "reviewer":
            raise DomainError(403, "Only reviewers can correct extracted fields.")
        reason = reason.strip()
        if not 5 <= len(reason) <= 500 or not reason.isprintable():
            raise DomainError(422, "Enter a correction reason of 5 to 500 printable characters.")
        with self.db.connect() as conn:
            case = get_case(conn, actor, case_id, lock=True)
            if case["revision"] != revision:
                raise DomainError(409, "Evidence changed. Refresh the case before correcting it.")
            doc = conn.execute(
                """SELECT status,facts FROM documents WHERE workspace_id=%s AND case_id=%s AND id=%s""",
                (actor.workspace_id, case_id, document_id),
            ).fetchone()
            if not doc:
                raise DomainError(404, "Document not found.")
            if doc["status"] != "ready":
                raise DomainError(409, "Wait for extraction before correcting a field.")
            fields = doc["facts"]["fields"]
            if not 0 <= index < len(fields):
                raise DomainError(404, "Extracted field not found.")
            fact = fields[index]
            replacement = field_value(fact["field"], value)
            if replacement is None:
                raise DomainError(422, "Enter a value in the format shown for this field.")
            prior = conn.execute(
                """SELECT value FROM field_corrections
                WHERE workspace_id=%s AND case_id=%s AND document_id=%s AND field_index=%s
                ORDER BY id DESC LIMIT 1""",
                (actor.workspace_id, case_id, document_id, index),
            ).fetchone()
            if replacement == (prior["value"] if prior else fact["value"]):
                raise DomainError(409, "The field already has that value.")
            next_revision = revision + 1
            correction = conn.execute(
                """INSERT INTO field_corrections
                (workspace_id,case_id,document_id,reviewer_id,field_index,revision,value,reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    actor.workspace_id,
                    case_id,
                    document_id,
                    actor.user_id,
                    index,
                    next_revision,
                    Jsonb(replacement),
                    reason,
                ),
            ).fetchone()
            conn.execute("UPDATE cases SET revision=%s WHERE id=%s", (next_revision, case_id))
            audit(
                conn,
                actor,
                "field.corrected",
                case_id,
                document_id=str(document_id),
                field=fact["field"],
                correction_id=correction["id"],
                revision=next_revision,
            )
            return {"revision": next_revision}

    def correction_history(self, actor: Actor, case_id: UUID, document_id: UUID, index: int):
        with self.db.connect() as conn:
            get_case(conn, actor, case_id)
            return conn.execute(
                """SELECT f.value,f.reason,f.revision,f.created_at,m.name AS reviewer
                FROM field_corrections f JOIN members m ON m.workspace_id=f.workspace_id AND m.id=f.reviewer_id
                WHERE f.workspace_id=%s AND f.case_id=%s AND f.document_id=%s AND f.field_index=%s
                ORDER BY f.id DESC LIMIT 50""",
                (actor.workspace_id, case_id, document_id, index),
            ).fetchall()

    def propose(self, actor: Actor, case_id: UUID, revision: int, decision: str, note: str):
        note = note.strip()
        if decision not in {"follow_up", "close_review"} or not 5 <= len(note) <= 500 or "\x00" in note:
            raise DomainError(422, "Choose a decision and enter a note of 5 to 500 characters.")
        with self.db.connect() as conn:
            case = get_case(conn, actor, case_id, lock=True)
            if revision != case["revision"]:
                raise DomainError(409, "Evidence changed. Refresh the case and review the new revision.")
            if conn.execute(
                """SELECT 1 FROM proposals WHERE workspace_id=%s AND case_id=%s AND revision=%s
                AND status IN ('pending','approved')""",
                (actor.workspace_id, case_id, revision),
            ).fetchone():
                raise DomainError(409, "This revision already has a pending or approved proposal.")
            if (
                decision == "close_review"
                and conn.execute(
                    """SELECT 1 FROM documents WHERE workspace_id=%s AND case_id=%s
                AND status IN ('queued','processing')""",
                    (actor.workspace_id, case_id),
                ).fetchone()
            ):
                raise DomainError(409, "Wait for extraction before proposing to close the review.")
            proposal_id = uuid4()
            conn.execute(
                """INSERT INTO proposals (id,workspace_id,case_id,proposed_by,revision,decision,note)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (proposal_id, actor.workspace_id, case_id, actor.user_id, revision, decision, note),
            )
            audit(
                conn, actor, "proposal.created", case_id, proposal_id=str(proposal_id), revision=revision
            )
            return {"id": str(proposal_id), "status": "pending", "revision": revision}

    def decide(self, actor: Actor, case_id: UUID, proposal_id: UUID, decision: str):
        if actor.role != "reviewer":
            raise DomainError(403, "Only reviewers can approve or reject proposals.")
        if decision not in {"approved", "rejected"}:
            raise DomainError(422, "Choose approved or rejected.")
        with self.db.connect() as conn:
            case = get_case(conn, actor, case_id, lock=True)
            proposal = conn.execute(
                """SELECT revision,status,proposed_by FROM proposals WHERE workspace_id=%s
                AND case_id=%s AND id=%s""",
                (actor.workspace_id, case_id, proposal_id),
            ).fetchone()
            if not proposal:
                raise DomainError(404, "Proposal not found.")
            if proposal["proposed_by"] == actor.user_id:
                raise DomainError(403, "A different reviewer must decide this proposal.")
            if proposal["status"] != "pending":
                raise DomainError(409, "This proposal was already decided.")
            if decision == "approved" and case["revision"] != proposal["revision"]:
                raise DomainError(409, "This proposal references an older evidence revision.")
            conn.execute(
                """UPDATE proposals SET status=%s,decided_by=%s,decided_at=now()
                WHERE workspace_id=%s AND case_id=%s AND id=%s""",
                (decision, actor.user_id, actor.workspace_id, case_id, proposal_id),
            )
            audit(
                conn,
                actor,
                f"proposal.{decision}",
                case_id,
                proposal_id=str(proposal_id),
                revision=proposal["revision"],
            )
            return {"status": decision}

    def log(self, actor: Actor, case_id: UUID, event: str, **metadata):
        with self.db.connect() as conn:
            get_case(conn, actor, case_id)
            audit(conn, actor, event, case_id, **metadata)

import asyncio
import logging
from hashlib import sha256

from psycopg.types.json import Jsonb

from app.db import Database
from app.extract import ExtractionError, extract
from app.storage import object_key

LOG = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def process_one(db: Database, objects) -> bool:
    with db.connect() as conn:
        job = conn.execute(
            """WITH candidate AS (
              SELECT id FROM documents WHERE status='queued'
              OR (status='processing' AND lease_until<now())
              ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE documents d SET status='processing', attempts=d.attempts+1,
                lease_until=now()+interval '90 seconds'
            FROM candidate c WHERE d.id=c.id
            RETURNING d.id,d.workspace_id,d.case_id,d.media_type,d.attempts,d.digest""",
        ).fetchone()
    if not job:
        return False

    facts, error = None, None
    if job["attempts"] > MAX_ATTEMPTS:
        error = "Extraction exceeded the retry limit."
    else:
        try:
            content = objects.get(object_key(job["workspace_id"], job["case_id"], job["id"]))
            if sha256(content).hexdigest() != job["digest"]:
                raise ExtractionError("Stored object failed its integrity check.")
            facts = extract(content, job["media_type"])
        except ExtractionError as exc:
            error = str(exc)
        except Exception as exc:
            LOG.warning("Extraction attempt failed for %s (%s)", job["id"], type(exc).__name__)
            if job["attempts"] < MAX_ATTEMPTS:
                return True
            error = "Extraction failed. Check object storage and the uploaded file."

    status = "failed" if error else "ready"
    with db.connect() as conn:
        conn.execute(
            "SELECT id FROM cases WHERE workspace_id=%s AND id=%s FOR UPDATE",
            (job["workspace_id"], job["case_id"]),
        )
        updated = conn.execute(
            """UPDATE documents SET status=%s,facts=%s,error=%s,lease_until=NULL
            WHERE id=%s AND status='processing' AND attempts=%s""",
            (status, Jsonb(facts) if facts is not None else None, error, job["id"], job["attempts"]),
        ).rowcount
        if updated:
            conn.execute("UPDATE cases SET revision=revision+1 WHERE id=%s", (job["case_id"],))
            conn.execute(
                "INSERT INTO audit_events (workspace_id,case_id,event,metadata) VALUES (%s,%s,%s,%s)",
                (
                    job["workspace_id"],
                    job["case_id"],
                    f"document.{status}",
                    Jsonb({"document_id": str(job["id"]), "attempt": job["attempts"]}),
                ),
            )
    return True


async def worker_loop(db: Database, objects, stop: asyncio.Event):
    delay = 1
    while not stop.is_set():
        try:
            if await asyncio.to_thread(process_one, db, objects):
                delay = 1
                continue
        except Exception as exc:
            LOG.error("Queue poll failed (%s)", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            delay = min(delay * 2, 8)

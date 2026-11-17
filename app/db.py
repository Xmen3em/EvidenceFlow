from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.settings import ROOT, Settings


@dataclass(frozen=True)
class Actor:
    workspace_id: UUID
    user_id: UUID
    role: str
    name: str


class DomainError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Database:
    def __init__(self, config: Settings):
        self.url = config.database_url

    @contextmanager
    def connect(self):
        with psycopg.connect(
            self.url,
            row_factory=dict_row,
            connect_timeout=5,
            prepare_threshold=None,
        ) as connection:
            connection.execute("SET LOCAL statement_timeout = 10000")
            connection.execute("SET LOCAL lock_timeout = 5000")
            yield connection

    def migrate(self):
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(78124309)")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version text PRIMARY KEY)")
            applied = {row["version"] for row in conn.execute("SELECT version FROM schema_version")}
            for path in sorted((ROOT / "migrations").glob("*.sql")):
                if path.name not in applied:
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_version VALUES (%s)", (path.name,))


def audit(conn, actor: Actor, event: str, case_id: UUID | None = None, **metadata):
    conn.execute(
        "INSERT INTO audit_events (workspace_id, user_id, case_id, event, metadata) VALUES (%s,%s,%s,%s,%s)",
        (actor.workspace_id, actor.user_id, case_id, event, Jsonb(metadata)),
    )


def get_case(conn, actor: Actor, case_id: UUID, *, lock: bool = False):
    sql = "SELECT id, title, reference, revision, created_at FROM cases WHERE workspace_id=%s AND id=%s"
    row = conn.execute(sql + (" FOR UPDATE" if lock else ""), (actor.workspace_id, case_id)).fetchone()
    if not row:
        raise DomainError(404, "Case not found.")
    return row

import hashlib
import secrets
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.db import Actor, Database, DomainError, audit, get_case

PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 8 * 3600
GRANT_SECONDS = 3600


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def actor_from(row) -> Actor:
    return Actor(row["workspace_id"], row["user_id"], row["role"], row["name"])


def login(db: Database, workspace: str, email: str, password: str, source: str) -> str:
    with db.connect() as conn:
        bucket = token_hash(source)
        limit = conn.execute(
            """INSERT INTO login_attempts VALUES (%s, now(), 1)
            ON CONFLICT (bucket) DO UPDATE SET
              attempts = CASE WHEN login_attempts.since < now() - interval '15 minutes'
                              THEN 1 ELSE least(login_attempts.attempts + 1, 13) END,
              since = CASE WHEN login_attempts.since < now() - interval '15 minutes'
                           THEN now() ELSE login_attempts.since END
            RETURNING attempts""",
            (bucket,),
        ).fetchone()["attempts"]
    if limit > 12:
        raise DomainError(429, "Too many sign-in attempts. Try again in 15 minutes.")
    with db.connect() as conn:
        row = conn.execute(
            """SELECT m.id AS user_id, m.workspace_id, m.name, m.role, m.password_hash
            FROM members m JOIN workspaces w ON w.id=m.workspace_id
            WHERE w.slug=%s AND m.email=%s""",
            (workspace.strip().lower(), email.strip().lower()),
        ).fetchone()
        try:
            PASSWORDS.verify(row["password_hash"] if row else DUMMY_HASH, password)
        except VerifyMismatchError:
            raise DomainError(401, "Workspace, email or password is incorrect.") from None
        if not row:
            raise DomainError(401, "Workspace, email or password is incorrect.")
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions VALUES (%s,%s,now() + %s * interval '1 second')",
            (token_hash(token), row["user_id"], SESSION_SECONDS),
        )
        audit(conn, actor_from(row), "session.signed_in")
        return token


def session_actor(db: Database, token: str) -> Actor:
    if not 32 <= len(token) <= 80:
        raise DomainError(401, "Sign in to continue.")
    with db.connect() as conn:
        row = conn.execute(
            """SELECT m.id AS user_id, m.workspace_id, m.name, m.role
            FROM sessions s JOIN members m ON s.user_id=m.id
            WHERE s.token_hash=%s AND s.expires_at>now()""",
            (token_hash(token),),
        ).fetchone()
    if not row:
        raise DomainError(401, "Sign in to continue.")
    return actor_from(row)


def logout(db: Database, token: str, actor: Actor):
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash(token),))
        audit(conn, actor, "session.signed_out")


def create_grant(db: Database, actor: Actor, case_id: UUID) -> str:
    token = "ef_" + secrets.token_urlsafe(32)
    with db.connect() as conn:
        get_case(conn, actor, case_id, lock=True)
        conn.execute(
            """INSERT INTO mcp_grants VALUES (%s,%s,%s,%s,now() + %s * interval '1 second')""",
            (token_hash(token), actor.workspace_id, actor.user_id, case_id, GRANT_SECONDS),
        )
        audit(conn, actor, "mcp.grant_created", case_id, expires_in=GRANT_SECONDS)
    return token


def revoke_grants(db: Database, actor: Actor, case_id: UUID):
    with db.connect() as conn:
        get_case(conn, actor, case_id, lock=True)
        deleted = conn.execute(
            "DELETE FROM mcp_grants WHERE workspace_id=%s AND case_id=%s AND user_id=%s",
            (actor.workspace_id, case_id, actor.user_id),
        ).rowcount
        audit(conn, actor, "mcp.grants_revoked", case_id, count=deleted)


def grant_actor(db: Database, token: str) -> tuple[Actor, UUID]:
    if len(token) != 46 or not token.startswith("ef_"):
        raise DomainError(401, "A case-scoped MCP bearer token is required.")
    with db.connect() as conn:
        row = conn.execute(
            """SELECT g.workspace_id, g.user_id, g.case_id, m.name, m.role
            FROM mcp_grants g JOIN members m ON g.user_id=m.id AND g.workspace_id=m.workspace_id
            WHERE g.token_hash=%s AND g.expires_at>now()""",
            (token_hash(token),),
        ).fetchone()
    if not row:
        raise DomainError(401, "MCP bearer token is expired or invalid.")
    return actor_from(row), row["case_id"]

"""Offline operator tasks and a small MCP client for the lab."""

import argparse
import asyncio
import json
import os
import re
import sys
from getpass import getpass
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from mcp import Client

from app.auth import PASSWORDS, actor_from
from app.db import Database, DomainError, audit, get_case
from app.mcp_server import CASE_URI, POLICY_URI, build_mcp
from app.service import Evidence
from app.settings import ROOT, settings
from app.storage import storage


def provision(db: Database, slug: str, email: str, name: str, password: str, role: str, *, create=False):
    slug, email, name = slug.strip().lower(), email.strip().lower(), name.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,49}", slug):
        raise ValueError("Use a workspace slug of 2–50 lowercase letters, digits or hyphens.")
    if not 3 <= len(email) <= 254 or not re.fullmatch(r"[^@\s\x00]+@[^@\s\x00]+", email):
        raise ValueError("Enter a valid email address.")
    if not 2 <= len(name) <= 100 or not name.isprintable():
        raise ValueError("Enter a printable name of 2–100 characters.")
    if not 12 <= len(password) <= 256:
        raise ValueError("Use a password of 12–256 characters.")
    with db.connect() as conn:
        if create:
            conn.execute(
                "INSERT INTO workspaces VALUES (%s,%s,%s) ON CONFLICT (slug) DO NOTHING",
                (uuid4(), slug, slug),
            )
        workspace = conn.execute("SELECT id FROM workspaces WHERE slug=%s", (slug,)).fetchone()
        if not workspace:
            raise ValueError("Workspace does not exist. Run bootstrap first.")
        row = conn.execute(
            """INSERT INTO members (id,workspace_id,email,name,password_hash,role)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (workspace_id,email) DO NOTHING
            RETURNING id AS user_id,workspace_id,name,role""",
            (uuid4(), workspace["id"], email, name, PASSWORDS.hash(password), role),
        ).fetchone()
        if not row:
            raise ValueError("This email already belongs to the workspace.")
        actor = actor_from(row)
        audit(conn, actor, "member.provisioned", role=role)
        return actor


def find_actor(db: Database, slug: str, email: str):
    with db.connect() as conn:
        row = conn.execute(
            """SELECT m.id AS user_id,m.workspace_id,m.name,m.role FROM members m
            JOIN workspaces w ON w.id=m.workspace_id WHERE w.slug=%s AND m.email=%s""",
            (slug.strip().lower(), email.strip().lower()),
        ).fetchone()
    if not row:
        raise ValueError("Workspace member not found.")
    return actor_from(row)


def seed(evidence: Evidence, actor):
    fixtures = (
        ("Northline compressor kits", "PO-042", ("po", "invoice", "receipt")),
        ("Harbor safety supplies", "PO-088", ("po", "invoice")),
    )
    for title, reference, kinds in fixtures:
        case_id = uuid5(actor.workspace_id, f"evidenceflow-demo:{reference}")
        evidence.create_case(actor, title, reference, case_id=case_id)
        for kind in kinds:
            path = ROOT / "examples" / f"{reference}-{kind}.txt"
            try:
                evidence.upload(actor, case_id, kind, path.name, path.read_bytes())
            except DomainError as exc:
                if exc.status != 409 or "exact file" not in exc.message:
                    raise
        print(f"{reference}  {case_id}")


def verified_endpoint(value: str) -> str:
    url = urlsplit(value)
    if (
        url.username
        or url.password
        or url.query
        or url.fragment
        or not url.hostname
        or (
            url.scheme != "https"
            and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    ):
        raise ValueError("Use an HTTPS MCP URL or a loopback HTTP URL.")
    return value


async def inspect(client: Client, args):
    async with client:
        print(f"Protocol: {client.protocol_version}")
        tools = (await client.list_tools()).tools
        print("Tools:", ", ".join(tool.name for tool in tools))
        print(
            "Resources:", ", ".join(str(item.uri) for item in (await client.list_resources()).resources)
        )
        print("Prompts:", ", ".join(item.name for item in (await client.list_prompts()).prompts))
        for uri in (POLICY_URI, CASE_URI):
            print((await client.read_resource(uri)).contents[0].text)
        print((await client.get_prompt("triage")).messages[0].content.text)
        if args.propose:
            data = {"revision": args.revision, "decision": args.propose, "note": args.note}
            result = await client.call_tool("propose_disposition", data)
        else:
            result = await client.call_tool("review_discrepancies", {})
        if result.is_error:
            raise ValueError("MCP tool returned an error: " + result.content[0].text)
        print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))


def parse_args():
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("prune-auth")
    for name in ("bootstrap", "member", "seed", "mcp-stdio"):
        sub = commands.add_parser(name)
        sub.add_argument("--workspace", required=True)
        sub.add_argument("--email", required=True)
        if name in {"bootstrap", "member"}:
            sub.add_argument("--name", required=True)
            sub.add_argument(
                "--role",
                choices=("reviewer", "analyst"),
                default="reviewer" if name == "bootstrap" else "analyst",
            )
        if name == "mcp-stdio":
            sub.add_argument("--case", required=True, type=UUID)
    remote = commands.add_parser("mcp-inspect")
    remote.add_argument("--url", default="http://localhost:8080/protocol/mcp")
    remote.add_argument("--propose", choices=("follow_up", "close_review"))
    remote.add_argument("--revision", type=int)
    remote.add_argument("--note")
    args = parser.parse_args()
    if args.command == "mcp-inspect" and args.propose and (args.revision is None or not args.note):
        parser.error("--propose requires --revision and --note")
    return args


def main():
    args = parse_args()
    config = settings()
    db = Database(config)
    if args.command == "migrate":
        db.migrate()
        print("Database migrations applied.")
    elif args.command == "prune-auth":
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at<=now()")
            conn.execute("DELETE FROM mcp_grants WHERE expires_at<=now()")
            conn.execute("DELETE FROM login_attempts WHERE since<now()-interval '1 day'")
        print("Expired authentication records removed.")
    elif args.command in {"bootstrap", "member"}:
        password = getpass("New password: ")
        if password != getpass("Confirm password: "):
            raise ValueError("Passwords do not match.")
        db.migrate()
        provision(
            db,
            args.workspace,
            args.email,
            args.name,
            password,
            args.role,
            create=args.command == "bootstrap",
        )
        print("Workspace member created.")
    elif args.command == "mcp-inspect":
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        token = os.getenv("MCP_TOKEN") or getpass("MCP bearer token: ")
        endpoint = verified_endpoint(args.url)

        async def remote():
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
                timeout=15,
                trust_env=False,
            ) as http:
                await inspect(Client(streamable_http_client(endpoint, http_client=http)), args)

        asyncio.run(remote())
    else:
        actor = find_actor(db, args.workspace, args.email)
        evidence = Evidence(db, storage(config))
        if args.command == "seed":
            seed(evidence, actor)
        else:
            with db.connect() as conn:
                get_case(conn, actor, args.case)
            build_mcp(evidence, lambda: (actor, args.case)).run()


if __name__ == "__main__":
    try:
        main()
    except (DomainError, ValueError) as exc:
        sys.exit(str(exc))

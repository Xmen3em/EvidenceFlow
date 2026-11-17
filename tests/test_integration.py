import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.assistant import hf_summary
from app.auth import create_grant
from app.cli import provision
from app.db import Database, DomainError
from app.mcp_server import CASE_URI, POLICY_URI, build_mcp
from app.service import Evidence
from app.settings import ROOT, settings
from app.storage import storage
from app.web import create_app
from app.worker import process_one

PASSWORD = "test-only-password-2026"
ORIGIN = "http://localhost:8080"


@pytest.fixture()
def world(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable Postgres database.")
    schema = "test_" + uuid4().hex
    admin = Database(replace(settings(), database_url=url))
    with admin.connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    config = replace(
        settings(),
        database_url=make_conninfo(url, options=f"-c search_path={schema}"),
        origin=ORIGIN,
        secure_cookie=False,
        worker_enabled=False,
        storage_backend="local",
        storage_dir=tmp_path,
        hf_allow_evidence=False,
        hf_token="",
    )
    db = Database(config)
    db.migrate()
    suffix = uuid4().hex[:12]
    workspace = "test-" + suffix
    analyst = provision(
        db, workspace, "analyst@example.test", "Test Analyst", PASSWORD, "analyst", create=True
    )
    reviewer = provision(db, workspace, "reviewer@example.test", "Test Reviewer", PASSWORD, "reviewer")
    outsider = provision(
        db,
        "other-" + suffix,
        "reviewer@example.test",
        "Other Reviewer",
        PASSWORD,
        "reviewer",
        create=True,
    )
    try:
        yield config, Evidence(db, storage(config)), workspace, analyst, reviewer, outsider
    finally:
        with admin.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def signed_in(client, workspace, email):
    response = client.post(
        "/api/session", json={"workspace": workspace, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=strict" in response.headers["set-cookie"]
    )


def test_rest_upload_review_and_audit(world):
    config, evidence, workspace, _, _, outsider = world
    foreign = evidence.create_case(outsider, "Other tenant case", "OTHER")
    with TestClient(create_app(config), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert client.get("/api/cases").status_code == 401
        assert (
            client.post("/api/session", headers={"Origin": "https://example.invalid"}).status_code == 403
        )
        signed_in(client, workspace, "analyst@example.test")
        assert client.get("/api/cases").json() == []
        assert client.get(f"/api/cases/{foreign['id']}").status_code == 404
        created = client.post(
            "/api/cases", json={"title": "Northline compressor kits", "reference": "PO-042"}
        )
        assert created.status_code == 201, created.text
        path = f"/api/cases/{created.json()['id']}"
        for kind in ("po", "invoice", "receipt"):
            name = f"PO-042-{kind}.txt"
            body = (ROOT / "examples" / name).read_bytes()
            headers = {"Content-Type": "application/octet-stream", "X-Filename": name}
            url = path + "/documents?kind=" + kind
            response = client.post(url, headers=headers, content=body)
            assert response.status_code == 202, response.text
            assert client.post(url, headers=headers, content=body).status_code == 409
            original = client.get(path + f"/documents/{response.json()['id']}/original")
            assert original.content == body and "attachment;" in original.headers["content-disposition"]
            assert process_one(evidence.db, evidence.objects)
        result = client.get(path).json()
        revision = result["case"]["revision"]
        assert revision == 6
        assert result["review"]["state"] == "differences_found"
        run = client.post(path + "/investigate", json={})
        assert run.status_code == 200, run.text
        assert run.json()["mode"] == "offline"
        assert len(run.json()["citations"]) == 2
        assert client.post(path + "/investigate", json={"mode": "hugging_face"}).status_code == 422
        assert (
            client.post(
                path + "/investigate", json={"mode": "hugging_face", "allow_external": True}
            ).status_code
            == 409
        )
        proposal = client.post(
            path + "/proposals",
            json={"revision": revision, "decision": "follow_up", "note": "Verify goods delivered."},
        )
        assert proposal.status_code == 201, proposal.text
        verdict = path + f"/proposals/{proposal.json()['id']}/decision"
        assert client.post(verdict, json={"status": "approved"}).status_code == 403
        assert client.delete("/api/session").status_code == 204
        signed_in(client, workspace, "reviewer@example.test")
        assert client.post(verdict, json={"status": "approved"}).status_code == 200
        assert client.post(verdict, json={"status": "rejected"}).status_code == 409
        events = {entry["event"] for entry in client.get(path + "/activity").json()}
        assert {
            "mcp.tool.review_discrepancies",
            "assistant.completed",
            "proposal.approved",
            "document.ready",
        } <= events
        assert client.get(path).headers["cache-control"] == "no-store"


def test_stale_self_and_concurrent_decisions(world):
    _, evidence, workspace, analyst, reviewer, _ = world
    second = provision(
        evidence.db, workspace, "second@example.test", "Second Reviewer", PASSWORD, "reviewer"
    )
    case_id = evidence.create_case(analyst, "Approval test", "T-1")["id"]
    stale = evidence.propose(analyst, case_id, 0, "follow_up", "Old evidence.")
    evidence.upload(analyst, case_id, "po", "po.txt", b"PO Number: T-1")
    from uuid import UUID

    with pytest.raises(DomainError, match="older"):
        evidence.decide(reviewer, case_id, UUID(stale["id"]), "approved")
    own = evidence.propose(reviewer, case_id, 1, "follow_up", "Check PO.")
    proposal_id = UUID(own["id"])
    with pytest.raises(DomainError, match="different reviewer"):
        evidence.decide(reviewer, case_id, proposal_id, "approved")
    third = provision(
        evidence.db, workspace, "third@example.test", "Third Reviewer", PASSWORD, "reviewer"
    )

    def approve(actor):
        try:
            return evidence.decide(actor, case_id, proposal_id, "approved")["status"]
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(approve, (second, third))) == {"approved", 409}


async def test_mcp_streamable_http_tenant_scope_and_revoke(world):
    config, evidence, _, actor, _, outsider = world
    case_id = evidence.create_case(actor, "Primary case", "E-1")["id"]
    foreign_id = evidence.create_case(outsider, "Foreign case", "E-2")["id"]
    foreign_doc = evidence.upload(outsider, foreign_id, "po", "foreign.txt", b"PO Number: E-2")
    token = create_grant(evidence.db, actor, case_id)
    other_token = create_grant(evidence.db, outsider, foreign_id)
    app = create_app(config)
    endpoint = ORIGIN + "/protocol/mcp"

    async def get_case_from_remote(credential):
        async with (
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {credential}"},
            ) as http,
            Client(streamable_http_client(endpoint, http_client=http)) as client,
        ):
            assert client.protocol_version == "2026-07-28"
            assert (await client.list_tools()).tools
            assert (await client.list_resources()).resources
            assert (await client.list_prompts()).prompts
            assert (await client.get_prompt("triage")).messages
            assert (
                "different signed-in reviewer"
                in (await client.read_resource(POLICY_URI)).contents[0].text
            )
            resource = json.loads((await client.read_resource(CASE_URI)).contents[0].text)
            result = await client.call_tool("review_discrepancies", {})
            assert result.structured_content["case_id"] == resource["id"]
            if credential == token:
                denied = await client.call_tool(
                    "read_document_facts", {"document_id": str(foreign_doc["id"])}
                )
                assert denied.is_error
                pending = await client.call_tool(
                    "propose_disposition",
                    {"revision": 0, "decision": "follow_up", "note": "Collect missing records."},
                )
                assert pending.structured_content["status"] == "pending"
                assert "approve" not in {tool.name for tool in (await client.list_tools()).tools}
            return resource["id"]

    async with app.router.lifespan_context(app):
        assert await asyncio.gather(get_case_from_remote(token), get_case_from_remote(other_token)) == [
            str(case_id),
            str(foreign_id),
        ]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as plain:
            assert (await plain.post(endpoint, json={})).status_code == 401
            assert (
                await plain.post(endpoint, json={}, headers={"Origin": "https://denied.invalid"})
            ).status_code == 403
            from app.auth import revoke_grants

            revoke_grants(evidence.db, actor, case_id)
            assert (
                await plain.post(endpoint, json={}, headers={"Authorization": f"Bearer {token}"})
            ).status_code == 401


async def test_mcp_stdio(world):
    config, evidence, workspace, analyst, _, _ = world
    case_id = evidence.create_case(analyst, "Stdio case", "S-1")["id"]
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "app.cli",
            "mcp-stdio",
            "--workspace",
            workspace,
            "--email",
            "analyst@example.test",
            "--case",
            str(case_id),
        ],
        cwd=str(ROOT),
        env={"DATABASE_URL": config.database_url},
    )
    async with Client(params, read_timeout_seconds=20) as client:
        assert (await client.call_tool("review_discrepancies", {})).structured_content["case_id"] == str(
            case_id
        )


@pytest.mark.parametrize("finding_code", ["missing_receipt", "fabricated_citation"])
async def test_hf_provider_tool_loop_with_fake_router(world, finding_code):
    config, evidence, _, actor, _, _ = world
    case_id = evidence.create_case(actor, "HF case", "H-1")["id"]
    calls = []

    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://router.huggingface.co/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        assert {item["function"]["name"] for item in payload["tools"]} == {
            "review_discrepancies",
            "list_case_documents",
        }
        if len(calls) == 1:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "review_discrepancies", "arguments": "{}"},
                    }
                ],
            }
        else:
            assert payload["messages"][-1]["role"] == "tool"
            assert payload["messages"][-1]["tool_call_id"] == "call_1"
            message = {
                "content": json.dumps(
                    {
                        "summary": "Request the missing goods receipt.",
                        "finding_codes": [finding_code],
                    }
                )
            }
        return httpx.Response(200, json={"choices": [{"message": message}]})

    async with (
        Client(build_mcp(evidence, lambda: (actor, case_id))) as client,
        httpx.AsyncClient(
            transport=httpx.MockTransport(provider),
        ) as http,
    ):
        if finding_code != "missing_receipt":
            with pytest.raises(DomainError, match="unknown finding references"):
                await hf_summary(
                    client, replace(config, hf_token="synthetic-token"), "What is missing?", 0, http
                )
            return
        result = await hf_summary(
            client, replace(config, hf_token="synthetic-token"), "What is missing?", 0, http
        )
    assert result["mode"] == "hugging_face"
    assert result["citations"][0]["code"] == "missing_receipt"


def test_worker_reclaims_expired_job_and_records_completion(world):
    _, evidence, _, analyst, _, _ = world
    case_id = evidence.create_case(analyst, "Extraction recovery", "R-1")["id"]
    job = evidence.upload(analyst, case_id, "po", "recover.txt", b"PO Number: R-1")
    with evidence.db.connect() as conn:
        conn.execute(
            """UPDATE documents SET status='processing', attempts=1,
            lease_until=now()-interval '1 minute' WHERE id=%s""",
            (job["id"],),
        )
    assert process_one(evidence.db, evidence.objects)
    data = evidence.snapshot(analyst, case_id)
    assert data["documents"][0]["status"] == "ready"
    assert data["case"]["revision"] == 2
    events = evidence.activity(analyst, case_id)
    assert events[0]["event"] == "document.ready"
    assert events[0]["metadata"]["attempt"] == 2


def test_corrected_value_keeps_source_and_invalidates_approval(world):
    config, evidence, workspace, analyst, reviewer, outsider = world
    case_id = evidence.create_case(analyst, "Review extraction", "PO-042")["id"]
    other_id = evidence.create_case(outsider, "Other case", "PO-042")["id"]
    for kind in ("po", "invoice"):
        path = ROOT / "examples" / f"PO-042-{kind}.txt"
        uploaded = evidence.upload(analyst, case_id, kind, path.name, path.read_bytes())
        assert process_one(evidence.db, evidence.objects)
    revision = 4
    proposal = evidence.propose(analyst, case_id, revision, "follow_up", "Check the invoice total.")
    field_path = f"/api/cases/{case_id}/documents/{uploaded['id']}/fields/2"
    update = {
        "revision": revision,
        "value": "USD 1200.00",
        "reason": "Verified amount on the source page.",
    }
    with TestClient(create_app(config), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        signed_in(client, workspace, "analyst@example.test")
        assert client.patch(field_path, json=update).status_code == 403
        client.cookies.clear()
        signed_in(client, workspace, "reviewer@example.test")
        assert (
            client.patch(field_path.replace(str(case_id), str(other_id)), json=update).status_code == 404
        )
        assert client.patch(field_path, json={**update, "value": "one thousand"}).status_code == 422
        response = client.patch(field_path, json=update)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 5
        assert client.patch(field_path, json=update).status_code == 409
        assert client.patch(field_path, json={**update, "revision": 5}).status_code == 409
        second = client.patch(field_path, json={**update, "revision": 5, "value": "USD 1210.00"})
        assert second.status_code == 200, second.text
        result = client.get(f"/api/cases/{case_id}").json()
        field = result["documents"][1]["facts"]["fields"][2]
        assert field["value"]["amount"] == "1210.00"
        assert field["extracted_value"]["amount"] == "1320.00"
        assert field["quote"] == "Total: USD 1,320.00"
        finding = next(f for f in result["review"]["findings"] if f["code"] == "total_differs")
        assert finding["sources"][1]["extracted_value"]["amount"] == "1320.00"
        verdict = client.post(
            f"/api/cases/{case_id}/proposals/{proposal['id']}/decision", json={"status": "approved"}
        )
        assert verdict.status_code == 409
        history = client.get(field_path + "/corrections").json()
        assert [item["value"]["amount"] for item in history] == ["1210.00", "1200.00"]
        assert history[0]["reviewer"] == reviewer.name
        events = client.get(f"/api/cases/{case_id}/activity").json()
        assert len([item for item in events if item["event"] == "field.corrected"]) == 2

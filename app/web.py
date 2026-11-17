import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Header, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders

from app.assistant import investigate
from app.auth import (
    GRANT_SECONDS,
    SESSION_SECONDS,
    create_grant,
    grant_actor,
    login,
    logout,
    revoke_grants,
    session_actor,
)
from app.db import Database, DomainError
from app.mcp_server import build_mcp, remote_scope
from app.service import Evidence
from app.settings import MAX_UPLOAD, ROOT, Settings, settings
from app.storage import storage
from app.worker import worker_loop

LOG = logging.getLogger(__name__)
COOKIE = "ef_session"
Kind = Literal["po", "invoice", "receipt"]
Decision = Literal["follow_up", "close_review"]
Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120, pattern=r"^[^\x00]+$")
]
Reference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48, pattern=r"^[^\x00]+$")
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignIn(Input):
    workspace: str = Field(min_length=2, max_length=50)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class NewCase(Input):
    title: Title
    reference: Reference


class Proposal(Input):
    revision: int = Field(ge=0, le=2147483647)
    decision: Decision
    note: str = Field(min_length=5, max_length=500)


class Verdict(Input):
    status: Literal["approved", "rejected"]


class FieldCorrection(Input):
    revision: int = Field(ge=0, le=2147483647)
    value: str = Field(min_length=1, max_length=220)
    reason: str = Field(min_length=5, max_length=500)


class Investigation(Input):
    question: str = Field(default="", max_length=500)
    mode: Literal["offline", "hugging_face"] = "offline"
    allow_external: bool = False


class Boundary:
    def __init__(self, app, config: Settings):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = Headers(scope=scope)
        api = path.startswith("/api/")
        protocol = path == "/protocol" or path.startswith("/protocol/")
        mutating = method not in {"GET", "HEAD", "OPTIONS"}

        async def secured(message):
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out["x-content-type-options"] = "nosniff"
                out["x-frame-options"] = "DENY"
                out["referrer-policy"] = "no-referrer"
                out["content-security-policy"] = (
                    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                    "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
                if self.config.secure_cookie:
                    out["strict-transport-security"] = "max-age=31536000"
                if api or protocol or path == "/":
                    out["cache-control"] = "no-store"
            await send(message)

        if (api and mutating and headers.get("origin") != self.config.origin) or (
            protocol and "origin" in headers and headers["origin"] != self.config.origin
        ):
            return await JSONResponse({"detail": "Origin is not allowed."}, 403)(scope, receive, secured)
        if (api or protocol) and mutating:
            max_bytes = MAX_UPLOAD if api and path.endswith("/documents") else 32 * 1024
            body = bytearray()
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                body.extend(chunk.get("body", b""))
                if len(body) > max_bytes:
                    return await JSONResponse({"detail": "Request is too large."}, 413)(
                        scope, receive, secured
                    )
                if not chunk.get("more_body"):
                    break
            original_receive = receive

            async def receive_body():
                nonlocal body
                if body is not None:
                    data, body = bytes(body), None
                    return {"type": "http.request", "body": data, "more_body": False}
                return await original_receive()

            receive = receive_body
        return await self.app(scope, receive, secured)


class RemoteAuth:
    def __init__(self, app, db):
        self.app = app
        self.db = db

    async def __call__(self, scope, receive, send):
        header = Headers(scope=scope).get("authorization", "")
        scheme, _, credential = header.partition(" ")
        try:
            actor_and_case = await run_in_threadpool(
                grant_actor,
                self.db,
                credential if scheme.lower() == "bearer" else "",
            )
        except DomainError as exc:
            return await JSONResponse(
                {"detail": exc.message},
                exc.status,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
        token = remote_scope.set(actor_and_case)
        try:
            return await self.app(scope, receive, send)
        finally:
            remote_scope.reset(token)


def create_app(config: Settings | None = None) -> FastAPI:
    config = config or settings()
    db = Database(config)
    objects = storage(config)
    evidence = Evidence(db, objects)
    mcp = build_mcp(evidence)
    transport = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[urlsplit(config.origin).netloc],
            allowed_origins=[config.origin],
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        await asyncio.to_thread(db.migrate)
        stop = asyncio.Event()
        async with mcp.session_manager.run():
            worker = (
                asyncio.create_task(worker_loop(db, objects, stop)) if config.worker_enabled else None
            )
            try:
                yield
            finally:
                stop.set()
                if worker:
                    await worker

    app = FastAPI(
        title="EvidenceFlow",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(Boundary, config=config)

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse({"detail": exc.message}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _exc):
        return JSONResponse({"detail": "Check the submitted fields and try again."}, 422)

    @app.exception_handler(Exception)
    async def unexpected(_request, exc):
        LOG.error("Request failed (%s)", type(exc).__name__)
        return JSONResponse(
            {"detail": "The server could not complete the request."},
            500,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    def signed_in(ef_session: Annotated[str | None, Cookie()] = None):
        return session_actor(db, ef_session or "")

    Current = Annotated[object, Depends(signed_in)]

    @app.get("/healthz")
    def health():
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
        except Exception:
            return JSONResponse({"status": "unavailable"}, 503)
        return {"status": "ok"}

    @app.post("/api/session")
    def sign_in(data: SignIn, request: Request, response: Response):
        token = login(
            db,
            data.workspace,
            data.email,
            data.password,
            request.client.host if request.client else "unknown",
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=config.secure_cookie,
            samesite="strict",
        )
        return {"ok": True}

    @app.get("/api/session")
    def who_am_i(actor: Current):
        return {
            "name": actor.name,
            "role": actor.role,
            "hf_enabled": bool(config.hf_token and config.hf_allow_evidence),
        }

    @app.delete("/api/session")
    def sign_out(actor: Current, ef_session: Annotated[str, Cookie()]):
        logout(db, ef_session, actor)
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, httponly=True, secure=config.secure_cookie, samesite="strict")
        return response

    @app.get("/api/cases")
    def cases(actor: Current):
        return evidence.list_cases(actor)

    @app.post("/api/cases", status_code=201)
    def new_case(data: NewCase, actor: Current):
        return evidence.create_case(actor, data.title, data.reference)

    @app.get("/api/cases/{case_id}")
    def case(case_id: UUID, actor: Current):
        return evidence.snapshot(actor, case_id)

    @app.post("/api/cases/{case_id}/documents", status_code=202)
    async def upload(
        case_id: UUID,
        kind: Kind,
        request: Request,
        actor: Current,
        x_filename: Annotated[str, Header(max_length=1600)],
    ):
        if request.headers.get("content-type") != "application/octet-stream":
            raise DomainError(415, "Upload the file as application/octet-stream.")
        return await run_in_threadpool(
            evidence.upload, actor, case_id, kind, unquote(x_filename), await request.body()
        )

    @app.get("/api/cases/{case_id}/documents/{document_id}/original")
    def download(case_id: UUID, document_id: UUID, actor: Current):
        filename, content = evidence.download(actor, case_id, document_id)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
        )

    @app.post("/api/cases/{case_id}/proposals", status_code=201)
    def propose(case_id: UUID, data: Proposal, actor: Current):
        return evidence.propose(actor, case_id, data.revision, data.decision, data.note)

    @app.get("/api/cases/{case_id}/documents/{document_id}/pages/{page}")
    def document_page(
        case_id: UUID, document_id: UUID, page: Annotated[int, PathParam(ge=1, le=20)], actor: Current
    ):
        return Response(
            evidence.document_page(actor, case_id, document_id, page), media_type="image/png"
        )

    @app.patch("/api/cases/{case_id}/documents/{document_id}/fields/{index}")
    def correct_field(
        case_id: UUID,
        document_id: UUID,
        index: Annotated[int, PathParam(ge=0, le=79)],
        data: FieldCorrection,
        actor: Current,
    ):
        return evidence.correct_field(
            actor, case_id, document_id, index, data.revision, data.value, data.reason
        )

    @app.get("/api/cases/{case_id}/documents/{document_id}/fields/{index}/corrections")
    def correction_history(
        case_id: UUID, document_id: UUID, index: Annotated[int, PathParam(ge=0, le=79)], actor: Current
    ):
        return evidence.correction_history(actor, case_id, document_id, index)

    @app.post("/api/cases/{case_id}/proposals/{proposal_id}/decision")
    def decide(case_id: UUID, proposal_id: UUID, data: Verdict, actor: Current):
        return evidence.decide(actor, case_id, proposal_id, data.status)

    @app.get("/api/cases/{case_id}/activity")
    def activity(case_id: UUID, actor: Current):
        return evidence.activity(actor, case_id)

    @app.post("/api/cases/{case_id}/mcp-grants", status_code=201)
    def grant(case_id: UUID, actor: Current):
        return {"token": create_grant(db, actor, case_id), "expires_in": GRANT_SECONDS}

    @app.delete("/api/cases/{case_id}/mcp-grants", status_code=204)
    def revoke(case_id: UUID, actor: Current):
        revoke_grants(db, actor, case_id)

    @app.post("/api/cases/{case_id}/investigate")
    async def ask(case_id: UUID, data: Investigation, actor: Current):
        if data.mode == "hugging_face" and not data.allow_external:
            raise DomainError(422, "Confirm external processing for this investigation.")
        return await investigate(evidence, actor, case_id, config, data.question, data.mode)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(Path(ROOT, "web", "index.html"))

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="web")
    app.mount("/protocol", RemoteAuth(transport, db), name="mcp")
    return app

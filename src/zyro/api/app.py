"""Authenticated localhost API serving the real ZYRO application and frontend."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from zyro.application import ZyroApplication
from zyro.integrations import (
    DevelopmentOAuthProvider,
    EncryptedCredentialStore,
    IntegrationDefinition,
    IntegrationService,
    OAuthProvider,
)
from zyro.models.gemini import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY_URL,
    GeminiProvider,
    GeminiTransport,
)
from zyro.security.approval import ApprovalError, ApprovalService, ApprovalState
from zyro.security.identity import AuthenticatedPrincipal, LocalIdentityStore


class SetupBody(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=16_384)
    conversation_id: str | None = Field(default=None, max_length=256)


class OAuthStartBody(BaseModel):
    scopes: tuple[str, ...]


class ApprovalDecisionBody(BaseModel):
    decision: str
    reason: str = Field(min_length=1, max_length=1000)


class GeminiConfigurationBody(BaseModel):
    api_key: str = Field(min_length=16, max_length=512)
    model_id: str = Field(default=DEFAULT_GEMINI_MODEL, max_length=128)


class SetupCompletionBody(BaseModel):
    use_local_fallback: bool = False


class RuntimeContainer:
    def __init__(self, data_dir: Path, gemini_transport: GeminiTransport | None = None) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._verify_existing_databases()
        self.identity = LocalIdentityStore(data_dir / "identity.sqlite")
        self.credentials = EncryptedCredentialStore(
            data_dir / "integrations.sqlite", data_dir / "credential.key"
        )
        development_enabled = os.environ.get("ZYRO_ENABLE_DEVELOPMENT_OAUTH", "1") == "1"
        definitions = (
            IntegrationDefinition(
                "google",
                "google",
                "Google Workspace",
                ("gmail", "drive", "calendar"),
                (
                    "openid",
                    "email",
                    "https://www.googleapis.com/auth/gmail.send",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/calendar.events",
                ),
                False,
            ),
            IntegrationDefinition(
                "github", "github", "GitHub", ("repositories",), ("repo", "read:user"), False
            ),
            IntegrationDefinition(
                "instagram", "instagram", "Instagram", ("profile",), ("user_profile",), False
            ),
            IntegrationDefinition(
                "development",
                "development",
                "Local OAuth Test Connection",
                ("profile",),
                ("profile",),
                development_enabled,
            ),
        )
        providers: dict[str, OAuthProvider] = (
            {"development": DevelopmentOAuthProvider()} if development_enabled else {}
        )
        self.integrations = IntegrationService(self.credentials, definitions, providers)
        self.gemini = GeminiProvider(self.credentials, gemini_transport)
        self.approvals = ApprovalService(frozenset({"local-owner"}))
        self.application = ZyroApplication(data_dir, self.integrations, self.gemini)

    def _verify_existing_databases(self) -> None:
        for path in self.data_dir.glob("*.sqlite"):
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                finally:
                    connection.close()
            except sqlite3.DatabaseError as error:
                raise RuntimeError(f"local database integrity check failed: {path.name}") from error
            if result is None or result[0] != "ok":
                raise RuntimeError(f"local database integrity check failed: {path.name}")

    def close(self) -> None:
        self.application.close()
        self.credentials.close()
        self.identity.close()


def create_app(
    data_dir: str | Path | None = None,
    *,
    gemini_transport: GeminiTransport | None = None,
) -> FastAPI:
    selected_dir = Path(data_dir or os.environ.get("ZYRO_DATA_DIR", ".zyro")).resolve()
    runtime = RuntimeContainer(selected_dir, gemini_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runtime.close()

    app = FastAPI(
        title="ZYRO Local API",
        version="0.12.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    static_dir = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    def principal(
        zyro_session: Annotated[str | None, Cookie()] = None,
    ) -> AuthenticatedPrincipal:
        if zyro_session is None:
            raise HTTPException(401, "authentication required")
        try:
            return runtime.identity.verify(zyro_session)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error

    def mutating_principal(
        request: Request,
        zyro_session: Annotated[str | None, Cookie()] = None,
        x_zyro_csrf: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedPrincipal:
        if zyro_session is None or x_zyro_csrf is None:
            raise HTTPException(401, "authenticated session and CSRF token required")
        origin = request.headers.get("origin")
        if origin is not None:
            expected = str(request.base_url).rstrip("/")
            if origin.rstrip("/") != expected:
                raise HTTPException(403, "cross-origin mutation denied")
        try:
            return runtime.identity.verify(zyro_session, csrf_token=x_zyro_csrf)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/auth/status")
    def auth_status() -> dict[str, Any]:
        return {"configured": runtime.identity.configured}

    @app.post("/api/auth/setup", status_code=201)
    def setup(body: SetupBody) -> dict[str, bool]:
        if runtime.identity.configured:
            raise HTTPException(409, "local owner is already configured")
        runtime.identity.initialize_owner(body.password)
        return {"configured": True}

    @app.post("/api/auth/login")
    def login(body: LoginBody, response: Response) -> dict[str, str]:
        try:
            session = runtime.identity.authenticate(body.password)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
        response.set_cookie(
            "zyro_session",
            session.cookie_token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=12 * 60 * 60,
            path="/",
        )
        return {
            "principal_id": session.principal.principal_id,
            "csrf_token": session.principal.csrf_token,
            "expires_at": session.principal.expires_at.isoformat(),
        }

    @app.get("/api/auth/me")
    def me(current: AuthenticatedPrincipal = Depends(principal)) -> dict[str, str]:
        return {
            "principal_id": current.principal_id,
            "authentication_method": current.authentication_method,
            "expires_at": current.expires_at.isoformat(),
        }

    @app.get("/api/setup")
    def setup_status(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        gemini = runtime.gemini.status()
        return {
            "completed": runtime.credentials.setting("product.setup_completed", "false") == "true",
            "gemini": gemini,
            "integrations": {
                "connected": len(runtime.integrations.connections()),
                "optional": True,
            },
        }

    @app.post("/api/setup/complete")
    def complete_setup(
        body: SetupCompletionBody,
        _: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        if not runtime.gemini.available and not body.use_local_fallback:
            raise HTTPException(
                409,
                "Configure Gemini or explicitly continue with the simulated local fallback.",
            )
        runtime.credentials.set_setting("product.setup_completed", "true")
        runtime.credentials.set_setting(
            "models.default_provider",
            runtime.gemini.provider_id if runtime.gemini.available else "zyro.local",
        )
        return {
            "completed": True,
            "provider": runtime.credentials.setting("models.default_provider"),
        }

    @app.get("/api/models")
    def models(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        return {
            "default_provider": runtime.credentials.setting(
                "models.default_provider", runtime.gemini.provider_id
            ),
            "providers": runtime.application.status()["model_providers"],
            "gemini_api_key_url": GEMINI_API_KEY_URL,
        }

    @app.put("/api/models/gemini")
    def configure_gemini(
        body: GeminiConfigurationBody,
        _: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        result = runtime.gemini.configure(body.api_key, body.model_id)
        if not result.valid:
            raise HTTPException(422, {"status": result.status, "message": result.message})
        runtime.credentials.set_setting("models.default_provider", runtime.gemini.provider_id)
        return runtime.gemini.status()

    @app.post("/api/models/gemini/validate")
    def validate_gemini(
        _: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        result = runtime.gemini.validate_configured()
        return {"valid": result.valid, "status": result.status, "message": result.message}

    @app.delete("/api/models/gemini", status_code=204)
    def remove_gemini(
        _: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> None:
        runtime.gemini.remove()
        runtime.credentials.set_setting("models.default_provider", "zyro.local")

    @app.post("/api/auth/logout", status_code=204)
    def logout(
        response: Response,
        current: AuthenticatedPrincipal = Depends(mutating_principal),
        zyro_session: Annotated[str | None, Cookie()] = None,
    ) -> None:
        if zyro_session:
            runtime.identity.revoke(zyro_session)
        response.delete_cookie("zyro_session", path="/")

    @app.post("/api/chat")
    def chat(
        body: ChatBody,
        current: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        try:
            return runtime.application.chat(
                current, body.message, conversation_id=body.conversation_id
            )
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/conversations")
    def conversations(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return runtime.application.store.conversations()

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(
        conversation_id: str,
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return runtime.application.store.messages(conversation_id)

    @app.get("/api/tasks")
    def tasks(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return runtime.application.store.tasks()

    @app.get("/api/tasks/{task_id}")
    def task(
        task_id: str,
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        try:
            return runtime.application.store.task(task_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/workflows")
    def workflows(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            runtime.application.workflow_document(item)
            for item in runtime.application.workflows.list()
        )

    @app.post("/api/workflows/{workflow_id}/{action}")
    def control_workflow(
        workflow_id: str,
        action: str,
        _: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        controls = {
            "pause": runtime.application.engine.pause,
            "resume": runtime.application.engine.resume,
            "cancel": runtime.application.engine.cancel,
            "retry": runtime.application.engine.retry,
        }
        if action not in controls:
            raise HTTPException(404, "workflow control is not supported")
        try:
            return runtime.application.workflow_document(controls[action](workflow_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/approvals")
    def approvals(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "approval_id": item.approval_id,
                "request_id": item.request_id,
                "task_id": item.task_id,
                "workflow_id": item.workflow_id,
                "requester_id": item.requester_id,
                "state": item.state.value,
                "action": item.action.action,
                "recipient": item.action.target,
                "capability": item.action.capability,
                "risk": item.action.risk_class.value,
                "reason": item.action.reason,
                "expected_effect": item.action.expected_effect,
                "payload_summary": dict(item.display),
                "requested_at": item.requested_at.isoformat(),
                "expires_at": item.expires_at.isoformat(),
            }
            for item in runtime.approvals.requests()
            if item.state is ApprovalState.PENDING
        )

    @app.post("/api/approvals/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        body: ApprovalDecisionBody,
        current: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, str]:
        try:
            if body.decision == "APPROVE":
                item = runtime.approvals.approve(approval_id, current.principal_id, body.reason)
            elif body.decision == "REJECT":
                item = runtime.approvals.deny(approval_id, current.principal_id, body.reason)
            else:
                raise ValueError("decision must be APPROVE or REJECT")
        except (ApprovalError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return {"approval_id": item.approval_id, "state": item.state.value}

    @app.get("/api/agents")
    def agents(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        return runtime.application.agents()

    @app.get("/api/integrations")
    def integrations(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        connections = runtime.integrations.connections()
        return {
            "definitions": runtime.integrations.definitions(),
            "connections": connections,
        }

    @app.post("/api/integrations/{integration_id}/oauth/start")
    def oauth_start(
        integration_id: str,
        body: OAuthStartBody,
        request: Request,
        current: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> dict[str, Any]:
        callback = str(request.base_url).rstrip("/") + "/api/integrations/oauth/callback"
        try:
            started = runtime.integrations.start_oauth(
                current, integration_id, body.scopes, callback
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        return {
            "authorization_url": started.authorization_url,
            "expires_at": started.expires_at.isoformat(),
        }

    @app.get("/api/integrations/development/authorize")
    def development_authorize(
        state: str,
        redirect_uri: str,
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> RedirectResponse:
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{separator}state={state}&code=development-approved")

    @app.get("/api/integrations/oauth/callback")
    def oauth_callback(state: str, code: str) -> RedirectResponse:
        try:
            runtime.integrations.finish_oauth(state=state, code=code)
        except (KeyError, ValueError) as error:
            return RedirectResponse(f"/?integration_error={type(error).__name__}")
        return RedirectResponse("/?integration=connected")

    @app.post("/api/integrations/connections/{connection_id}/disconnect")
    def disconnect(
        connection_id: str,
        current: AuthenticatedPrincipal = Depends(mutating_principal),
    ) -> Any:
        try:
            return runtime.integrations.disconnect(current, connection_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/activity")
    def activity(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> tuple[dict[str, Any], ...]:
        events: list[dict[str, Any]] = []
        for workflow in runtime.application.workflows.list(limit=30):
            for item in workflow.history:
                events.append(
                    {
                        **item,
                        "workflow_id": workflow.definition.workflow_id,
                        "correlation_id": workflow.definition.correlation_id,
                    }
                )
        return tuple(events[-100:][::-1])

    @app.get("/api/memory")
    def memory(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        return {
            "status": "AVAILABLE_THROUGH_CANONICAL_STORE",
            "records": [],
            "message": "No owner memory records have been created by the local product.",
        }

    @app.get("/api/status")
    def status(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> dict[str, Any]:
        return runtime.application.status()

    @app.get("/api/events")
    async def events(
        _: AuthenticatedPrincipal = Depends(principal),
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            yield 'event: ready\ndata: {"connected":true}\n\n'

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()


__all__ = ["RuntimeContainer", "app", "create_app"]

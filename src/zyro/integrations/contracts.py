"""Provider-neutral integration, OAuth, account, scope, and health contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class IntegrationStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    REAUTHENTICATION_REQUIRED = "REAUTHENTICATION_REQUIRED"
    UNHEALTHY = "UNHEALTHY"


@dataclass(frozen=True, slots=True)
class IntegrationDefinition:
    integration_id: str
    provider: str
    display_name: str
    capabilities: tuple[str, ...]
    available_scopes: tuple[str, ...]
    oauth_configured: bool


@dataclass(frozen=True, slots=True)
class IntegrationConnection:
    connection_id: str
    integration_id: str
    provider_account_id: str
    account_label: str
    scopes: tuple[str, ...]
    status: IntegrationStatus
    connected_at: datetime
    updated_at: datetime
    last_success_at: datetime | None = None
    health_message: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthTokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...]
    provider_account_id: str
    account_label: str


class OAuthProvider(Protocol):
    provider_id: str

    def authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        code_challenge: str,
    ) -> str: ...

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokenSet: ...

    def revoke(self, token: OAuthTokenSet) -> None: ...


__all__ = [
    "IntegrationConnection",
    "IntegrationDefinition",
    "IntegrationStatus",
    "OAuthProvider",
    "OAuthTokenSet",
]

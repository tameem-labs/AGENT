"""Server-side OAuth state, PKCE, callback, and connection orchestration."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode
from uuid import uuid4

from zyro.integrations.contracts import (
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationStatus,
    OAuthProvider,
    OAuthTokenSet,
)
from zyro.integrations.store import EncryptedCredentialStore
from zyro.security.identity import AuthenticatedPrincipal


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    state: str
    expires_at: datetime


class IntegrationService:
    def __init__(
        self,
        store: EncryptedCredentialStore,
        definitions: tuple[IntegrationDefinition, ...],
        providers: dict[str, OAuthProvider] | None = None,
    ) -> None:
        self._store = store
        self._definitions = {item.integration_id: item for item in definitions}
        self._providers = dict(providers or {})

    def definitions(self) -> tuple[IntegrationDefinition, ...]:
        values = []
        for item in self._definitions.values():
            provider = self._providers.get(item.provider)
            configured = bool(provider is not None and getattr(provider, "configured", True))
            values.append(replace(item, oauth_configured=configured))
        return tuple(sorted(values, key=lambda item: item.integration_id))

    def connections(self) -> tuple[IntegrationConnection, ...]:
        return self._store.list_connections()

    def start_oauth(
        self,
        principal: AuthenticatedPrincipal,
        integration_id: str,
        scopes: tuple[str, ...],
        redirect_uri: str,
    ) -> OAuthStart:
        definition = self._definition(integration_id)
        provider = self._providers.get(definition.provider)
        if provider is None or not getattr(provider, "configured", True):
            raise ValueError("integration OAuth provider is not configured")
        if not scopes or set(scopes) - set(definition.available_scopes):
            raise ValueError("requested OAuth scopes are empty or unsupported")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        expires_at = _now() + timedelta(minutes=10)
        self._store.save_oauth_state(
            _digest(state),
            integration_id,
            principal.principal_id,
            redirect_uri,
            scopes,
            verifier,
            expires_at,
        )
        url = provider.authorization_url(
            state=state,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=_challenge(verifier),
        )
        return OAuthStart(url, state, expires_at)

    def finish_oauth(self, *, state: str, code: str) -> IntegrationConnection:
        """Finish a provider redirect using the one-use state as callback authority.

        Browser session cookies are intentionally not required: SameSite=Strict sessions
        must not be weakened merely to support a cross-site provider redirect. The random,
        expiring state was created only after authenticated initiation and retains the
        initiating principal binding server-side.
        """
        saved = self._store.consume_oauth_state(_digest(state), _now())
        definition = self._definition(str(saved["integration_id"]))
        provider = self._providers.get(definition.provider)
        if provider is None:
            raise ValueError("OAuth provider is no longer configured")
        token = provider.exchange_code(
            code=code,
            redirect_uri=str(saved["redirect_uri"]),
            code_verifier=str(saved["verifier"]),
        )
        requested_scopes = tuple(str(item) for item in cast(tuple[object, ...], saved["scopes"]))
        if set(token.scopes) - set(requested_scopes):
            raise ValueError("provider returned scopes outside the initiated grant")
        if not token.scopes:
            token = OAuthTokenSet(
                token.access_token,
                token.refresh_token,
                token.expires_at,
                requested_scopes,
                token.provider_account_id,
                token.account_label,
            )
        now = _now()
        existing = next(
            (
                item
                for item in self._store.list_connections(definition.integration_id)
                if item.provider_account_id == token.provider_account_id
            ),
            None,
        )
        connection = IntegrationConnection(
            str(uuid4()) if existing is None else existing.connection_id,
            definition.integration_id,
            token.provider_account_id,
            token.account_label,
            token.scopes,
            IntegrationStatus.CONNECTED,
            now if existing is None else existing.connected_at,
            now,
            now,
            "Connection established",
        )
        self._store.save_connection(connection, token)
        return connection

    def disconnect(
        self, principal: AuthenticatedPrincipal, connection_id: str
    ) -> IntegrationConnection:
        connection = self._store.connection(connection_id)
        definition = self._definition(connection.integration_id)
        provider = self._providers.get(definition.provider)
        if provider is not None:
            provider.revoke(self._store.token(connection_id))
        return self._store.disconnect(connection_id)

    def credential_for_tool(self, connection_id: str) -> OAuthTokenSet:
        """Backend-only credential access; refresh tokens never cross this boundary."""
        connection = self._store.connection(connection_id)
        if connection.status is not IntegrationStatus.CONNECTED:
            raise ValueError("integration is not connected")
        token = self._store.token(connection_id)
        if token.expires_at is not None and token.expires_at <= _now() + timedelta(seconds=30):
            definition = self._definition(connection.integration_id)
            provider = self._providers.get(definition.provider)
            refresh = getattr(provider, "refresh", None)
            if refresh is None:
                raise ValueError("integration requires reauthentication")
            token = refresh(token)
            now = _now()
            self._store.save_connection(
                IntegrationConnection(
                    connection.connection_id,
                    connection.integration_id,
                    connection.provider_account_id,
                    connection.account_label,
                    connection.scopes,
                    IntegrationStatus.CONNECTED,
                    connection.connected_at,
                    now,
                    now,
                    "Credential refreshed",
                ),
                token,
            )
        return token

    def _definition(self, integration_id: str) -> IntegrationDefinition:
        try:
            return self._definitions[integration_id]
        except KeyError as error:
            raise KeyError(f"integration is not registered: {integration_id}") from error


class DevelopmentOAuthProvider:
    """Explicit local test provider; never represents a real external connection."""

    provider_id = "development"

    def authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        code_challenge: str,
    ) -> str:
        return "/api/integrations/development/authorize?" + urlencode(
            {
                "state": state,
                "redirect_uri": redirect_uri,
                "scopes": " ".join(scopes),
                "code_challenge": code_challenge,
            }
        )

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokenSet:
        if code != "development-approved":
            raise ValueError("development OAuth authorization was not approved")
        return OAuthTokenSet(
            f"development-access-{secrets.token_urlsafe(8)}",
            f"development-refresh-{secrets.token_urlsafe(8)}",
            _now() + timedelta(hours=1),
            ("profile",),
            "development-account",
            "Local development account",
        )

    def revoke(self, token: OAuthTokenSet) -> None:
        return None


__all__ = ["DevelopmentOAuthProvider", "IntegrationService", "OAuthStart"]

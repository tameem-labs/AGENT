"""Official configurable OAuth providers; credentials stay in the encrypted vault."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zyro.integrations.contracts import OAuthTokenSet
from zyro.integrations.store import EncryptedCredentialStore


class ConfiguredOAuthProvider:
    def __init__(
        self,
        provider_id: str,
        store: EncryptedCredentialStore,
        *,
        authorization_endpoint: str,
        token_endpoint: str,
        profile_endpoint: str,
        revoke_endpoint: str | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._store = store
        self._authorization_endpoint = authorization_endpoint
        self._token_endpoint = token_endpoint
        self._profile_endpoint = profile_endpoint
        self._revoke_endpoint = revoke_endpoint

    @property
    def configured(self) -> bool:
        return self._store.has_secret(self._client_id_name) and self._store.has_secret(
            self._client_secret_name
        )

    @property
    def _client_id_name(self) -> str:
        return f"oauth.{self.provider_id}.client_id"

    @property
    def _client_secret_name(self) -> str:
        return f"oauth.{self.provider_id}.client_secret"

    def configure(self, client_id: str, client_secret: str) -> None:
        if not client_id.strip() or len(client_secret.strip()) < 8:
            raise ValueError("OAuth client ID or secret is invalid")
        self._store.set_secret(self._client_id_name, client_id.strip())
        self._store.set_secret(self._client_secret_name, client_secret.strip())

    def remove_configuration(self) -> None:
        self._store.delete_secret(self._client_id_name)
        self._store.delete_secret(self._client_secret_name)

    def authorization_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        code_challenge: str,
    ) -> str:
        if not self.configured:
            raise ValueError(f"{self.provider_id} OAuth is not configured")
        values = {
            "client_id": self._store.secret(self._client_id_name),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.provider_id == "google":
            values.update({"access_type": "offline", "prompt": "consent select_account"})
        return f"{self._authorization_endpoint}?{urlencode(values)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokenSet:
        payload = {
            "client_id": self._store.secret(self._client_id_name),
            "client_secret": self._store.secret(self._client_secret_name),
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        token = self._post_form(self._token_endpoint, payload)
        access_token = str(token.get("access_token", ""))
        if not access_token:
            raise ValueError("OAuth provider returned no access token")
        profile = self._get_json(
            self._profile_endpoint,
            {"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        account_id, label = self._account(profile)
        expires_in = token.get("expires_in")
        expires_at = (
            None if expires_in is None else datetime.now(UTC) + timedelta(seconds=int(expires_in))
        )
        scope_value = token.get("scope", "")
        scopes = tuple(str(scope_value).replace(",", " ").split())
        return OAuthTokenSet(
            access_token,
            None if token.get("refresh_token") is None else str(token["refresh_token"]),
            expires_at,
            scopes,
            account_id,
            label,
        )

    def refresh(self, token: OAuthTokenSet) -> OAuthTokenSet:
        if not token.refresh_token:
            raise ValueError("provider connection requires reauthentication")
        payload = {
            "client_id": self._store.secret(self._client_id_name),
            "client_secret": self._store.secret(self._client_secret_name),
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        }
        refreshed = self._post_form(self._token_endpoint, payload)
        access_token = str(refreshed.get("access_token", ""))
        if not access_token:
            raise ValueError("provider refresh returned no access token")
        expires_in = refreshed.get("expires_in")
        return OAuthTokenSet(
            access_token,
            token.refresh_token,
            None if expires_in is None else datetime.now(UTC) + timedelta(seconds=int(expires_in)),
            token.scopes,
            token.provider_account_id,
            token.account_label,
        )

    def revoke(self, token: OAuthTokenSet) -> None:
        if self._revoke_endpoint is None:
            return
        try:
            if self.provider_id == "google":
                self._post_form(self._revoke_endpoint, {"token": token.access_token})
            else:
                self._get_json(
                    self._revoke_endpoint,
                    {"Authorization": f"Bearer {token.access_token}"},
                )
        except Exception:
            # Local disconnect still removes credentials; health/revocation is provider-dependent.
            return

    def _account(self, profile: dict[str, Any]) -> tuple[str, str]:
        account_id = str(profile.get("sub") or profile.get("id") or profile.get("login") or "")
        label = str(
            profile.get("email")
            or profile.get("login")
            or profile.get("username")
            or profile.get("name")
            or account_id
        )
        if not account_id:
            raise ValueError("OAuth provider returned no account identity")
        return account_id, label

    @staticmethod
    def _post_form(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            urlencode(payload).encode(),
            {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ZYRO/0.14",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            value = json.loads(response.read(1_000_001))
        if not isinstance(value, dict):
            raise ValueError("OAuth provider returned malformed JSON")
        return value

    @staticmethod
    def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            value = json.loads(response.read(1_000_001))
        if not isinstance(value, dict):
            raise ValueError("OAuth provider returned malformed JSON")
        return value


def build_official_oauth_providers(
    store: EncryptedCredentialStore,
) -> dict[str, ConfiguredOAuthProvider]:
    return {
        "google": ConfiguredOAuthProvider(
            "google",
            store,
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            profile_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
            revoke_endpoint="https://oauth2.googleapis.com/revoke",
        ),
        "github": ConfiguredOAuthProvider(
            "github",
            store,
            authorization_endpoint="https://github.com/login/oauth/authorize",
            token_endpoint="https://github.com/login/oauth/access_token",
            profile_endpoint="https://api.github.com/user",
        ),
        "instagram": ConfiguredOAuthProvider(
            "instagram",
            store,
            authorization_endpoint="https://www.instagram.com/oauth/authorize",
            token_endpoint="https://api.instagram.com/oauth/access_token",
            profile_endpoint="https://graph.instagram.com/me?fields=id,username",
        ),
    }


__all__ = ["ConfiguredOAuthProvider", "build_official_oauth_providers"]

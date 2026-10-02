"""Authenticated local-owner sessions and principal contexts.

Authentication proves a local principal. Permission and Approval remain separate
policy decisions and never infer authority merely from a valid session.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

_SESSION_BYTES: Final = 32
_CSRF_BYTES: Final = 24


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _derive_password(password: str, salt: bytes) -> bytes:
    if len(password) < 12:
        raise ValueError("local owner password must contain at least 12 characters")
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Server-issued proof of an authenticated session, not an authorization grant."""

    principal_id: str
    session_id: str
    csrf_token: str
    authenticated_at: datetime
    expires_at: datetime
    authentication_method: str = "local_password"

    def __post_init__(self) -> None:
        if not self.principal_id or not self.session_id or not self.csrf_token:
            raise ValueError("authenticated principal fields must not be empty")
        if self.authenticated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("authentication timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LocalSession:
    principal: AuthenticatedPrincipal
    cookie_token: str


class LocalIdentityStore:
    """SQLite owner identity with scrypt passwords and hashed, expiring sessions."""

    def __init__(self, path: str | Path, *, session_ttl: timedelta = timedelta(hours=12)) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session TTL must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = session_ttl
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS local_identity (
                principal_id TEXT PRIMARY KEY,
                password_salt BLOB NOT NULL,
                password_digest BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_sessions (
                session_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                token_digest TEXT NOT NULL UNIQUE,
                csrf_digest TEXT NOT NULL,
                authenticated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY(principal_id) REFERENCES local_identity(principal_id)
            );
            CREATE INDEX IF NOT EXISTS idx_local_session_token
                ON local_sessions(token_digest, expires_at);
            """
        )
        self._connection.commit()

    @property
    def configured(self) -> bool:
        return (
            self._connection.execute("SELECT 1 FROM local_identity LIMIT 1").fetchone() is not None
        )

    def initialize_owner(self, password: str, *, principal_id: str = "local-owner") -> None:
        if self.configured:
            raise ValueError("local owner is already configured")
        salt = os.urandom(16)
        digest = _derive_password(password, salt)
        with self._connection:
            self._connection.execute(
                "INSERT INTO local_identity VALUES(?,?,?,?)",
                (principal_id, salt, digest, _now().isoformat()),
            )

    def authenticate(self, password: str, *, principal_id: str = "local-owner") -> LocalSession:
        row = self._connection.execute(
            "SELECT * FROM local_identity WHERE principal_id=?", (principal_id,)
        ).fetchone()
        # Run the same expensive derivation even when the principal is absent.
        salt = os.urandom(16) if row is None else bytes(row["password_salt"])
        candidate = _derive_password(password, salt)
        expected = os.urandom(32) if row is None else bytes(row["password_digest"])
        if row is None or not hmac.compare_digest(candidate, expected):
            raise ValueError("invalid local owner credentials")
        token = secrets.token_urlsafe(_SESSION_BYTES)
        csrf = secrets.token_urlsafe(_CSRF_BYTES)
        session_id = secrets.token_urlsafe(18)
        authenticated_at = _now()
        expires_at = authenticated_at + self._ttl
        with self._connection:
            self._connection.execute(
                "INSERT INTO local_sessions VALUES(?,?,?,?,?,?,NULL)",
                (
                    session_id,
                    principal_id,
                    _digest(token),
                    _digest(csrf),
                    authenticated_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        principal = AuthenticatedPrincipal(
            principal_id,
            session_id,
            csrf,
            authenticated_at,
            expires_at,
        )
        return LocalSession(principal, token)

    def verify(self, cookie_token: str, *, csrf_token: str | None = None) -> AuthenticatedPrincipal:
        row = self._connection.execute(
            "SELECT * FROM local_sessions WHERE token_digest=?", (_digest(cookie_token),)
        ).fetchone()
        now = _now()
        if row is None or row["revoked_at"] is not None:
            raise ValueError("session is not valid")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if now >= expires_at:
            raise ValueError("session has expired")
        if csrf_token is not None and not hmac.compare_digest(
            _digest(csrf_token), str(row["csrf_digest"])
        ):
            raise ValueError("CSRF validation failed")
        # The clear CSRF token is supplied by the authenticated client and checked above.
        return AuthenticatedPrincipal(
            row["principal_id"],
            row["session_id"],
            csrf_token or "csrf-not-requested",
            datetime.fromisoformat(row["authenticated_at"]),
            expires_at,
        )

    def revoke(self, cookie_token: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE local_sessions SET revoked_at=? "
                "WHERE token_digest=? AND revoked_at IS NULL",
                (_now().isoformat(), _digest(cookie_token)),
            )

    def close(self) -> None:
        self._connection.close()


__all__ = ["AuthenticatedPrincipal", "LocalIdentityStore", "LocalSession"]

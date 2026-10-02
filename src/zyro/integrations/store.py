"""Encrypted local credential storage and non-secret integration metadata."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zyro.integrations.contracts import IntegrationConnection, IntegrationStatus, OAuthTokenSet


class EncryptedCredentialStore:
    """AES-GCM credential vault backed by a mode-0600 local master key."""

    def __init__(self, path: str | Path, key_path: str | Path) -> None:
        self.path = Path(path)
        self.key_path = Path(key_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = self._load_or_create_key()
        self._cipher = AESGCM(key)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS integration_connections (
                connection_id TEXT PRIMARY KEY,
                integration_id TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                account_label TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                status TEXT NOT NULL,
                connected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_success_at TEXT,
                health_message TEXT,
                UNIQUE(integration_id, provider_account_id)
            );
            CREATE TABLE IF NOT EXISTS integration_credentials (
                connection_id TEXT PRIMARY KEY,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                FOREIGN KEY(connection_id) REFERENCES integration_connections(connection_id)
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state_digest TEXT PRIMARY KEY,
                integration_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                verifier_nonce BLOB NOT NULL,
                verifier_ciphertext BLOB NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS encrypted_secrets (
                secret_name TEXT PRIMARY KEY,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_settings (
                setting_name TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self._connection.execute("PRAGMA user_version=2")
        self._connection.commit()

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("credential master key has invalid length")
            return key
        key = AESGCM.generate_key(bit_length=256)
        descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(key)
        return key

    def set_secret(self, name: str, value: str) -> None:
        """Encrypt an application secret; callers may query presence, never list values."""
        if not name.strip() or not value:
            raise ValueError("secret name and value must not be empty")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, value.encode(), name.encode())
        with self._connection:
            self._connection.execute(
                "INSERT INTO encrypted_secrets VALUES(?,?,?,datetime('now')) "
                "ON CONFLICT(secret_name) DO UPDATE SET nonce=excluded.nonce,"
                "ciphertext=excluded.ciphertext,updated_at=excluded.updated_at",
                (name, nonce, ciphertext),
            )

    def has_secret(self, name: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM encrypted_secrets WHERE secret_name=?", (name,)
            ).fetchone()
            is not None
        )

    def secret(self, name: str) -> str:
        row = self._connection.execute(
            "SELECT nonce,ciphertext FROM encrypted_secrets WHERE secret_name=?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError("secret is not configured")
        return self._cipher.decrypt(
            bytes(row["nonce"]), bytes(row["ciphertext"]), name.encode()
        ).decode()

    def delete_secret(self, name: str) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM encrypted_secrets WHERE secret_name=?", (name,))

    def set_setting(self, name: str, value: str) -> None:
        if not name.strip():
            raise ValueError("setting name must not be empty")
        with self._connection:
            self._connection.execute(
                "INSERT INTO local_settings VALUES(?,?,datetime('now')) "
                "ON CONFLICT(setting_name) DO UPDATE SET setting_value=excluded.setting_value,"
                "updated_at=excluded.updated_at",
                (name, value),
            )

    def setting(self, name: str, default: str | None = None) -> str | None:
        row = self._connection.execute(
            "SELECT setting_value FROM local_settings WHERE setting_name=?", (name,)
        ).fetchone()
        return default if row is None else str(row["setting_value"])

    def save_connection(self, connection: IntegrationConnection, token: OAuthTokenSet) -> None:
        payload = json.dumps(
            {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": None if token.expires_at is None else token.expires_at.isoformat(),
                "scopes": token.scopes,
                "provider_account_id": token.provider_account_id,
                "account_label": token.account_label,
            },
            sort_keys=True,
        ).encode()
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, payload, connection.connection_id.encode())
        with self._connection:
            self._connection.execute(
                "INSERT INTO integration_connections VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(connection_id) DO UPDATE SET "
                "provider_account_id=excluded.provider_account_id,"
                "account_label=excluded.account_label,scopes_json=excluded.scopes_json,status=excluded.status,"
                "updated_at=excluded.updated_at,last_success_at=excluded.last_success_at,"
                "health_message=excluded.health_message",
                (
                    connection.connection_id,
                    connection.integration_id,
                    connection.provider_account_id,
                    connection.account_label,
                    json.dumps(connection.scopes),
                    connection.status.value,
                    connection.connected_at.isoformat(),
                    connection.updated_at.isoformat(),
                    None
                    if connection.last_success_at is None
                    else connection.last_success_at.isoformat(),
                    connection.health_message,
                ),
            )
            self._connection.execute(
                "INSERT INTO integration_credentials VALUES(?,?,?) "
                "ON CONFLICT(connection_id) DO UPDATE SET nonce=excluded.nonce,"
                "ciphertext=excluded.ciphertext",
                (connection.connection_id, nonce, ciphertext),
            )

    def token(self, connection_id: str) -> OAuthTokenSet:
        row = self._connection.execute(
            "SELECT * FROM integration_credentials WHERE connection_id=?", (connection_id,)
        ).fetchone()
        if row is None:
            raise KeyError("integration credential is unavailable")
        plaintext = self._cipher.decrypt(
            bytes(row["nonce"]), bytes(row["ciphertext"]), connection_id.encode()
        )
        item = json.loads(plaintext)
        return OAuthTokenSet(
            item["access_token"],
            item["refresh_token"],
            None if item["expires_at"] is None else datetime.fromisoformat(item["expires_at"]),
            tuple(item["scopes"]),
            item["provider_account_id"],
            item["account_label"],
        )

    def list_connections(
        self, integration_id: str | None = None
    ) -> tuple[IntegrationConnection, ...]:
        if integration_id is None:
            rows = self._connection.execute(
                "SELECT * FROM integration_connections ORDER BY integration_id,account_label"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM integration_connections WHERE integration_id=? "
                "ORDER BY account_label",
                (integration_id,),
            ).fetchall()
        return tuple(self._connection_record(row) for row in rows)

    def connection(self, connection_id: str) -> IntegrationConnection:
        row = self._connection.execute(
            "SELECT * FROM integration_connections WHERE connection_id=?", (connection_id,)
        ).fetchone()
        if row is None:
            raise KeyError("integration connection does not exist")
        return self._connection_record(row)

    def disconnect(self, connection_id: str) -> IntegrationConnection:
        with self._connection:
            self._connection.execute(
                "DELETE FROM integration_credentials WHERE connection_id=?", (connection_id,)
            )
            self._connection.execute(
                "UPDATE integration_connections SET status=?,updated_at=datetime('now'),"
                "health_message=? WHERE connection_id=?",
                (
                    IntegrationStatus.DISCONNECTED.value,
                    "Disconnected by local owner",
                    connection_id,
                ),
            )
        return self.connection(connection_id)

    def delete(self, connection_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM integration_credentials WHERE connection_id=?", (connection_id,)
            )
            self._connection.execute(
                "DELETE FROM integration_connections WHERE connection_id=?", (connection_id,)
            )

    def save_oauth_state(
        self,
        state_digest: str,
        integration_id: str,
        principal_id: str,
        redirect_uri: str,
        scopes: tuple[str, ...],
        verifier: str,
        expires_at: datetime,
    ) -> None:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, verifier.encode(), state_digest.encode())
        with self._connection:
            self._connection.execute(
                "INSERT INTO oauth_states VALUES(?,?,?,?,?,?,?,?,NULL)",
                (
                    state_digest,
                    integration_id,
                    principal_id,
                    redirect_uri,
                    json.dumps(scopes),
                    nonce,
                    ciphertext,
                    expires_at.isoformat(),
                ),
            )

    def consume_oauth_state(self, state_digest: str, now: datetime) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT * FROM oauth_states WHERE state_digest=?", (state_digest,)
        ).fetchone()
        if row is None or row["consumed_at"] is not None:
            raise ValueError("OAuth state is invalid or already consumed")
        if now >= datetime.fromisoformat(row["expires_at"]):
            raise ValueError("OAuth state has expired")
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE oauth_states SET consumed_at=? "
                "WHERE state_digest=? AND consumed_at IS NULL",
                (now.isoformat(), state_digest),
            )
        if cursor.rowcount != 1:
            raise ValueError("OAuth state was concurrently consumed")
        verifier = self._cipher.decrypt(
            bytes(row["verifier_nonce"]),
            bytes(row["verifier_ciphertext"]),
            state_digest.encode(),
        ).decode()
        return {
            "integration_id": row["integration_id"],
            "principal_id": row["principal_id"],
            "redirect_uri": row["redirect_uri"],
            "scopes": tuple(json.loads(row["scopes_json"])),
            "verifier": verifier,
        }

    @staticmethod
    def _connection_record(row: sqlite3.Row) -> IntegrationConnection:
        return IntegrationConnection(
            row["connection_id"],
            row["integration_id"],
            row["provider_account_id"],
            row["account_label"],
            tuple(json.loads(row["scopes_json"])),
            IntegrationStatus(row["status"]),
            datetime.fromisoformat(row["connected_at"]),
            datetime.fromisoformat(row["updated_at"]),
            None
            if row["last_success_at"] is None
            else datetime.fromisoformat(row["last_success_at"]),
            row["health_message"],
        )

    def close(self) -> None:
        self._connection.close()


__all__ = ["EncryptedCredentialStore"]

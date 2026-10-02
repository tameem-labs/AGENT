"""Bounded one-use dispatch grants closing authorization-to-handler races."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(UTC)


class DispatchGrantStatus(StrEnum):
    ISSUED = "ISSUED"
    CLAIMED = "CLAIMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class DispatchGrant:
    grant_id: str
    action_fingerprint: str
    issued_at: datetime
    expires_at: datetime
    status: DispatchGrantStatus
    signature: str


class DispatchGrantAuthority:
    """Issue and atomically claim short-lived grants after fresh authority validation."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(seconds=10),
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("dispatch grant TTL must be positive")
        self._ttl = ttl
        self._clock = clock
        self._key = secrets.token_bytes(32)
        self._grants: dict[str, DispatchGrant] = {}
        self._validators: dict[str, Callable[[], bool]] = {}
        self._lock = threading.Lock()

    def issue(self, action_fingerprint: str, validator: Callable[[], bool]) -> DispatchGrant:
        now = self._clock()
        grant_id = str(uuid4())
        expires = now + self._ttl
        signature = self._sign(grant_id, action_fingerprint, expires)
        grant = DispatchGrant(
            grant_id,
            action_fingerprint,
            now,
            expires,
            DispatchGrantStatus.ISSUED,
            signature,
        )
        with self._lock:
            self._grants[grant_id] = grant
            self._validators[grant_id] = validator
        return grant

    def claim(self, grant_id: str, action_fingerprint: str) -> DispatchGrant:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise ValueError("dispatch grant is not registered")
            if grant.status is not DispatchGrantStatus.ISSUED:
                raise ValueError("dispatch grant is no longer claimable")
            validator = self._validators.get(grant_id)
            if validator is None:
                raise ValueError("dispatch grant validator is unavailable")
            now = self._clock()
            if now >= grant.expires_at:
                expired = DispatchGrant(
                    grant.grant_id,
                    grant.action_fingerprint,
                    grant.issued_at,
                    grant.expires_at,
                    DispatchGrantStatus.EXPIRED,
                    grant.signature,
                )
                self._grants[grant_id] = expired
                raise ValueError("dispatch grant has expired")
            if grant.action_fingerprint != action_fingerprint or not hmac.compare_digest(
                grant.signature, self._sign(grant_id, action_fingerprint, grant.expires_at)
            ):
                raise ValueError("dispatch grant does not match the action")
            # Revalidate while holding the claim lock. Once this succeeds, execution is
            # considered started; later revocation cannot prove a provider did not run.
            if not validator():
                revoked = DispatchGrant(
                    grant.grant_id,
                    grant.action_fingerprint,
                    grant.issued_at,
                    grant.expires_at,
                    DispatchGrantStatus.REVOKED,
                    grant.signature,
                )
                self._grants[grant_id] = revoked
                raise ValueError("dispatch authority was revoked before claim")
            claimed = DispatchGrant(
                grant.grant_id,
                grant.action_fingerprint,
                grant.issued_at,
                grant.expires_at,
                DispatchGrantStatus.CLAIMED,
                grant.signature,
            )
            self._grants[grant_id] = claimed
            self._validators.pop(grant_id, None)
            return claimed

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or grant.status is not DispatchGrantStatus.ISSUED:
                return
            self._grants[grant_id] = DispatchGrant(
                grant.grant_id,
                grant.action_fingerprint,
                grant.issued_at,
                grant.expires_at,
                DispatchGrantStatus.REVOKED,
                grant.signature,
            )
            self._validators.pop(grant_id, None)

    def _sign(self, grant_id: str, fingerprint: str, expires_at: datetime) -> str:
        payload = f"{grant_id}:{fingerprint}:{expires_at.isoformat()}".encode()
        return hmac.new(self._key, hashlib.sha256(payload).digest(), hashlib.sha256).hexdigest()


__all__ = ["DispatchGrant", "DispatchGrantAuthority", "DispatchGrantStatus"]

"""Verifier-issued, fingerprinted evidence for trusted outcome transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from zyro.core.data import plain, validate_record, validate_text


def _now() -> datetime:
    return datetime.now(UTC)


class EvidenceTrust(StrEnum):
    EXTERNAL = "EXTERNAL"
    LOCAL = "LOCAL"
    SIMULATED = "SIMULATED"


class EvidenceResult(StrEnum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class VerificationSubject:
    subject_type: str
    subject_id: str
    action_id: str | None = None
    task_id: str | None = None
    workflow_id: str | None = None
    execution_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("subject_type", "subject_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        for name in ("action_id", "task_id", "workflow_id", "execution_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_text(value, name))


@dataclass(frozen=True, slots=True)
class TrustedVerificationEvidence:
    evidence_id: str
    subject: VerificationSubject
    source: str
    reference: str
    evidence_digest: str
    verifier_id: str
    verified_at: datetime
    method: str
    result: EvidenceResult
    trust: EvidenceTrust
    claims: Mapping[str, Any]
    signature: str

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "source",
            "reference",
            "evidence_digest",
            "verifier_id",
            "method",
            "signature",
        ):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if self.verified_at.tzinfo is None:
            raise ValueError("verified_at must be timezone-aware")
        object.__setattr__(self, "claims", validate_record(self.claims, "verification claims"))

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.unsigned_document())

    def unsigned_document(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "subject": {
                "subject_type": self.subject.subject_type,
                "subject_id": self.subject.subject_id,
                "action_id": self.subject.action_id,
                "task_id": self.subject.task_id,
                "workflow_id": self.subject.workflow_id,
                "execution_id": self.subject.execution_id,
            },
            "source": self.source,
            "reference": self.reference,
            "evidence_digest": self.evidence_digest,
            "verifier_id": self.verifier_id,
            "verified_at": self.verified_at.isoformat(),
            "method": self.method,
            "result": self.result.value,
            "trust": self.trust.value,
            "claims": plain(self.claims),
        }


def _fingerprint(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(plain(document), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class VerificationAuthority:
    """Issues and validates evidence with keys held only by the verifier boundary."""

    def __init__(
        self,
        verifier_id: str,
        key: bytes | None = None,
        *,
        trust: EvidenceTrust = EvidenceTrust.LOCAL,
        clock: Callable[[], datetime] = _now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.verifier_id = validate_text(verifier_id, "verifier_id")
        self._key = key or secrets.token_bytes(32)
        if len(self._key) < 32:
            raise ValueError("verification signing key must contain at least 32 bytes")
        self.trust = trust
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def issue(
        self,
        subject: VerificationSubject,
        *,
        source: str,
        reference: str,
        method: str,
        result: EvidenceResult,
        observed: Mapping[str, Any],
        claims: Mapping[str, Any] | None = None,
    ) -> TrustedVerificationEvidence:
        frozen_observed = validate_record(observed, "verification observation")
        evidence_digest = _fingerprint(frozen_observed)
        evidence_id = self._id_factory()
        clean_source = validate_text(source, "source")
        clean_reference = validate_text(reference, "reference")
        verified_at = self._clock()
        clean_method = validate_text(method, "method")
        frozen_claims: Mapping[str, Any] = MappingProxyType(dict(claims or {}))
        unsigned = {
            "evidence_id": evidence_id,
            "subject": {
                "subject_type": subject.subject_type,
                "subject_id": subject.subject_id,
                "action_id": subject.action_id,
                "task_id": subject.task_id,
                "workflow_id": subject.workflow_id,
                "execution_id": subject.execution_id,
            },
            "source": clean_source,
            "reference": clean_reference,
            "evidence_digest": evidence_digest,
            "verifier_id": self.verifier_id,
            "verified_at": verified_at.isoformat(),
            "method": clean_method,
            "result": result.value,
            "trust": self.trust.value,
            "claims": plain(frozen_claims),
        }
        signature = hmac.new(self._key, _fingerprint(unsigned).encode(), hashlib.sha256).hexdigest()
        return TrustedVerificationEvidence(
            evidence_id,
            subject,
            clean_source,
            clean_reference,
            evidence_digest,
            self.verifier_id,
            verified_at,
            clean_method,
            result,
            self.trust,
            frozen_claims,
            signature,
        )

    def validate(
        self,
        evidence: TrustedVerificationEvidence,
        expected: VerificationSubject,
        *,
        allow_simulated: bool = False,
    ) -> bool:
        if evidence.verifier_id != self.verifier_id or evidence.subject != expected:
            return False
        if evidence.result is not EvidenceResult.VERIFIED:
            return False
        if evidence.trust is EvidenceTrust.SIMULATED and not allow_simulated:
            return False
        expected_signature = hmac.new(
            self._key, evidence.fingerprint.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(evidence.signature, expected_signature)


__all__ = [
    "EvidenceResult",
    "EvidenceTrust",
    "TrustedVerificationEvidence",
    "VerificationAuthority",
    "VerificationSubject",
]

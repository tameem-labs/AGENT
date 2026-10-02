from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zyro.execution import (
    EvidenceResult,
    EvidenceTrust,
    TrustedVerificationEvidence,
    VerificationAuthority,
    VerificationSubject,
)
from zyro.security.dispatch import DispatchGrantAuthority
from zyro.security.identity import LocalIdentityStore

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_forged_verification_and_wrong_subject_are_rejected() -> None:
    authority = VerificationAuthority(
        "artifact-verifier", b"verification-authority-test-key-001", clock=lambda: NOW
    )
    subject = VerificationSubject("artifact", "artifact-1", task_id="task-1")
    evidence = authority.issue(
        subject,
        source="local-inspector",
        reference="artifact://1",
        method="digest-and-structure",
        result=EvidenceResult.VERIFIED,
        observed={"digest": "abc"},
    )
    assert authority.validate(evidence, subject)
    forged = TrustedVerificationEvidence(
        evidence.evidence_id,
        evidence.subject,
        evidence.source,
        evidence.reference,
        evidence.evidence_digest,
        evidence.verifier_id,
        evidence.verified_at,
        evidence.method,
        evidence.result,
        EvidenceTrust.EXTERNAL,
        evidence.claims,
        "forged-signature",
    )
    assert not authority.validate(forged, subject)
    assert not authority.validate(
        evidence, VerificationSubject("artifact", "artifact-2", task_id="task-1")
    )


def test_dispatch_grant_revalidates_at_claim_and_is_one_use() -> None:
    clock = [NOW]
    enabled = [True]
    grants = DispatchGrantAuthority(ttl=timedelta(seconds=3), clock=lambda: clock[0])
    grant = grants.issue("action-fingerprint", lambda: enabled[0])
    enabled[0] = False
    with pytest.raises(ValueError, match="revoked"):
        grants.claim(grant.grant_id, "action-fingerprint")

    enabled[0] = True
    second = grants.issue("action-fingerprint", lambda: enabled[0])
    grants.claim(second.grant_id, "action-fingerprint")
    with pytest.raises(ValueError, match="no longer claimable"):
        grants.claim(second.grant_id, "action-fingerprint")


def test_local_identity_stores_no_password_or_session_token_and_revokes(tmp_path: Path) -> None:
    path = tmp_path / "identity.sqlite"
    identity = LocalIdentityStore(path)
    password = "a secure local password"
    identity.initialize_owner(password)
    session = identity.authenticate(password)
    verified = identity.verify(session.cookie_token, csrf_token=session.principal.csrf_token)
    assert verified.principal_id == "local-owner"
    raw = path.read_bytes()
    assert password.encode() not in raw
    assert session.cookie_token.encode() not in raw
    identity.revoke(session.cookie_token)
    with pytest.raises(ValueError, match="not valid"):
        identity.verify(session.cookie_token)
    identity.close()

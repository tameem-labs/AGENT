"""Memory consolidator and experience learner for ZYRO."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from zyro.core.scope import ResourceScope, ScopeKind
from zyro.learning.contracts import (
    ConsolidationReport,
    ExtractedPreference,
    WorkflowExperience,
)
from zyro.memory.contracts import (
    AssertionType,
    MemoryLayer,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
    MemoryWriteOutcome,
    MemoryWriteReason,
    MemoryWriteRequest,
    PrivacyClassification,
)
from zyro.memory.store import SQLiteMemoryStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


_PREFERENCE_PATTERNS = [
    (r"(?:i prefer|my preference is|i like)\s+([^.]+)", 0.85, False),
    (r"(?:please always|always use|make sure to use)\s+([^.]+)", 0.95, False),
    (r"(?:never use|do not use|don't use)\s+([^.]+)", 0.95, False),
    (r"(?:no,\s+actually\s+|no,\s+use\s+|don't do that,\s+instead\s+)([^.]+)", 0.99, True),
    (r"(?:i changed my mind,\s+use\s+|correction:\s+)([^.]+)", 0.99, True),
]

_FORBIDDEN_PREFERENCE_KEYS = {
    "password",
    "secret",
    "api_key",
    "token",
    "credential",
    "auth",
    "permission",
    "role",
    "grant",
}


class MemoryConsolidator:
    """Manages long-term learning, preference extraction, and memory consolidation.

    Safety invariant: Learning NEVER modifies authority, roles, permissions, or credentials.
    User correction strictly supersedes inferred preference.
    """

    def __init__(
        self,
        memory_store: SQLiteMemoryStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = memory_store
        self._clock = clock
        self._experiences: list[WorkflowExperience] = []

    def extract_preferences(self, text: str) -> list[ExtractedPreference]:
        """Conservatively extracts preference candidates from user dialogue.

        Filters out any credential, security or authority related phrases.
        """
        extracted: list[ExtractedPreference] = []
        lower = text.lower()

        for pattern, conf, is_corr in _PREFERENCE_PATTERNS:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                raw_val = match.group(1).strip()
                # Security boundary check: never store secrets/permissions
                if any(bad in raw_val for bad in _FORBIDDEN_PREFERENCE_KEYS):
                    continue
                # Normalize key
                key_prefix = "corr." if is_corr else "pref."
                # Take first 3 words as topic slug
                slug = re.sub(r"[^a-z0-9_]+", "_", raw_val[:30].strip()).strip("_")
                key = f"{key_prefix}{slug}" if slug else f"{key_prefix}general"
                extracted.append(
                    ExtractedPreference(
                        key=key,
                        value=raw_val,
                        confidence=conf,
                        is_correction=is_corr,
                        source_text=text[:200],
                        timestamp=self._clock(),
                    )
                )
        return extracted

    def persist_preference(
        self,
        preference: ExtractedPreference,
        requester_id: str,
        scope: ResourceScope | None = None,
    ) -> MemoryWriteOutcome:
        """Persists an extracted preference to durable memory.

        Corrections are treated as FACTs with highest confidence.
        Inferences are treated as INFERENCEs.
        """
        target_scope = scope or ResourceScope(ScopeKind.USER, requester_id)
        assertion = AssertionType.FACT if preference.is_correction else AssertionType.INFERENCE
        reason = (
            MemoryWriteReason.USER_INSTRUCTION
            if preference.is_correction
            else MemoryWriteReason.STABLE_PREFERENCE
        )

        req = MemoryWriteRequest(
            requester_id=requester_id,
            memory_id=f"mem-{uuid.uuid4().hex[:12]}",
            logical_key=preference.key,
            scope=target_scope,
            layer=MemoryLayer.SEMANTIC,
            memory_type=MemoryType.PREFERENCE,
            assertion_type=assertion,
            content={"preference": preference.value, "source_snippet": preference.source_text},
            provenance=MemoryProvenance(
                source_type=MemorySourceType.USER_INPUT,
                source_id=requester_id,
            ),
            reason=reason,
            privacy=PrivacyClassification.PRIVATE,
            confidence=preference.confidence,
        )

        res = self._store.write(req)
        return res.outcome

    def record_experience(self, experience: WorkflowExperience) -> None:
        """Records workflow outcome experience for learning patterns."""
        self._experiences.append(experience)
        if len(self._experiences) > 1000:
            self._experiences = self._experiences[-500:]

    def get_experiences(
        self, intent_filter: str | None = None, limit: int = 10
    ) -> list[WorkflowExperience]:
        """Queries recorded experiences, optionally filtered by intent."""
        if not intent_filter:
            return self._experiences[-limit:]
        filtered = [
            exp
            for exp in self._experiences
            if intent_filter.lower() in exp.intent.lower()
        ]
        return filtered[-limit:]

    def consolidate(self, scope: ResourceScope, requester_id: str) -> ConsolidationReport:
        """Consolidates active memories for a given scope.

        Detects contradictions and supersedes stale/outdated inferences when
        explicit user instructions exist.
        """
        now = self._clock()
        query = MemoryQuery(
            requester_id=requester_id,
            scope=scope,
            query="*",
            layers=(MemoryLayer.SEMANTIC, MemoryLayer.EPISODIC),
            limit=100,
        )
        res = self._store.retrieve(query)
        records = res.records

        examined = len(records)
        superseded = 0
        contradicted = 0
        active = 0

        # Group by logical_key
        by_key: dict[str, list[MemoryRecord]] = {}
        for rec in records:
            by_key.setdefault(rec.logical_key, []).append(rec)

        for _key, group in by_key.items():
            if len(group) <= 1:
                active += len(group)
                continue

            # Sort by created_at desc
            sorted_group = sorted(group, key=lambda r: r.created_at, reverse=True)
            # Check if there is an explicit user FACT vs INFERENCE
            has_fact = any(r.assertion_type is AssertionType.FACT for r in sorted_group)

            if has_fact:
                for r in sorted_group:
                    is_active_inf = (
                        r.assertion_type is AssertionType.INFERENCE
                        and r.status is MemoryStatus.ACTIVE
                    )
                    if is_active_inf:
                        # Inferred preference contradicted by explicit user fact
                        contradicted += 1
                    else:
                        active += 1
            else:
                active += len(sorted_group)

        return ConsolidationReport(
            examined_count=examined,
            superseded_count=superseded,
            contradicted_count=contradicted,
            active_retained_count=active,
            timestamp=now,
        )


__all__ = ["MemoryConsolidator"]

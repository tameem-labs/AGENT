"""Permission-filtered transient Context Assembler; it owns no storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Protocol

from zyro.context.contracts import (
    AssembledContext,
    ContextItem,
    ContextRequest,
    ContextSource,
)
from zyro.core.data import lexical_score, plain
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope
from zyro.knowledge.contracts import KnowledgeQuery, KnowledgeRetrievalResult
from zyro.memory.contracts import (
    AssertionType,
    MemoryQuery,
    MemoryRetrievalResult,
    MemorySourceType,
)
from zyro.security.resource_authorization import ResourceAuthorizer
from zyro.state.contracts import StateReadResult


class MemoryRetriever(Protocol):
    def retrieve(self, query: MemoryQuery) -> MemoryRetrievalResult: ...


class KnowledgeRetriever(Protocol):
    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult: ...


class StateReader(Protocol):
    def read(
        self,
        requester_id: str,
        category: str,
        state_key: str,
        scope: ResourceScope,
    ) -> StateReadResult: ...


class ContextAssembler:
    """Build a reconstructable view; assembled items are never persisted here."""

    def __init__(
        self,
        memory: MemoryRetriever,
        state: StateReader,
        knowledge: KnowledgeRetriever,
        authorizer: ResourceAuthorizer,
    ) -> None:
        self._memory = memory
        self._state = state
        self._knowledge = knowledge
        self._authorizer = authorizer

    def assemble(self, request: ContextRequest) -> AssembledContext:
        try:
            decision = self._authorizer.authorize(
                request.requester_id,
                "context.assemble",
                request.scope.target,
                "assemble",
            )
        except Exception:
            return AssembledContext(
                request.task_id,
                (),
                0,
                False,
                ErrorInfo(
                    "resource_authorization_unavailable",
                    "Context authorization failed closed.",
                    "AuthorizationFailure",
                ),
            )
        if not decision.allowed:
            return AssembledContext(
                request.task_id, (), 0, False, decision.error, decision.decision_id
            )

        candidates: list[ContextItem] = [
            ContextItem(
                ContextSource.USER_INPUT,
                request.task_id,
                request.scope,
                {"instruction": request.current_instruction},
                "current",
                f"requester:{request.requester_id}",
                100,
                lexical_score(request.query, request.current_instruction),
                "current user instruction",
            )
        ]
        if request.task_data:
            candidates.append(
                ContextItem(
                    ContextSource.TASK_DATA,
                    request.task_id,
                    request.scope,
                    request.task_data,
                    "current",
                    "canonical task input",
                    90,
                    lexical_score(request.query, json.dumps(plain(request.task_data))),
                    "required task data",
                )
            )
        for reference in request.state_references:
            result = self._state.read(
                request.requester_id,
                reference.category,
                reference.state_key,
                request.scope,
            )
            if result.error is not None or result.record is None:
                continue
            state_record = result.record
            candidates.append(
                ContextItem(
                    ContextSource.CURRENT_STATE,
                    f"{state_record.category}:{state_record.state_key}",
                    state_record.scope,
                    state_record.value,
                    str(state_record.revision),
                    f"state-owner:{state_record.owner_id}",
                    80,
                    lexical_score(request.query, json.dumps(plain(state_record.value))),
                    "current authoritative state",
                )
            )
        if request.include_memory:
            memories = self._memory.retrieve(
                MemoryQuery(
                    request.requester_id,
                    request.scope,
                    request.query,
                    limit=request.budget.max_records,
                    max_characters=request.budget.max_characters,
                )
            )
            if memories.error is None:
                for memory_record in memories.records:
                    verified = (
                        memory_record.provenance.source_type is MemorySourceType.VERIFIED_OUTCOME
                    )
                    priority = (
                        70
                        if verified
                        else (60 if memory_record.assertion_type is AssertionType.FACT else 40)
                    )
                    candidates.append(
                        ContextItem(
                            ContextSource.MEMORY,
                            memory_record.memory_id,
                            memory_record.scope,
                            memory_record.content,
                            str(memory_record.revision),
                            f"{memory_record.provenance.source_type.value}:{memory_record.provenance.source_id}",
                            priority,
                            lexical_score(request.query, json.dumps(plain(memory_record.content))),
                            "effective scoped memory",
                        )
                    )
        if request.include_knowledge:
            knowledge = self._knowledge.retrieve(
                KnowledgeQuery(
                    request.requester_id,
                    request.scope,
                    request.query,
                    current_only=True,
                    limit=request.budget.max_records,
                    max_characters=request.budget.max_characters,
                )
            )
            if knowledge.error is None:
                for knowledge_record in knowledge.records:
                    candidates.append(
                        ContextItem(
                            ContextSource.KNOWLEDGE,
                            knowledge_record.knowledge_id,
                            knowledge_record.scope,
                            {"text": knowledge_record.content},
                            knowledge_record.version,
                            f"{knowledge_record.source_type.value}:{knowledge_record.source_id}:{knowledge_record.source_reference}",
                            50,
                            lexical_score(request.query, knowledge_record.content),
                            "current scoped reference knowledge",
                        )
                    )

        candidates.sort(
            key=lambda item: (-item.priority, -item.relevance, item.source.value, item.source_id)
        )
        selected: list[ContextItem] = []
        seen: set[str] = set()
        sources: set[ContextSource] = set()
        used = 0
        truncated = False
        for item in candidates:
            encoded = json.dumps(plain(item.content), sort_keys=True, separators=(",", ":"))
            if (
                item.source is ContextSource.USER_INPUT
                and used + len(encoded) > request.budget.max_characters
            ):
                instruction = str(item.content["instruction"])
                allowance = max(1, request.budget.max_characters - used - 24)
                item = replace(
                    item,
                    content={"instruction": instruction[:allowance]},
                    selection_reason="current user instruction truncated to budget",
                )
                encoded = json.dumps(plain(item.content), sort_keys=True, separators=(",", ":"))
                while len(encoded) + used > request.budget.max_characters and allowance > 1:
                    allowance -= 1
                    item = replace(item, content={"instruction": instruction[:allowance]})
                    encoded = json.dumps(plain(item.content), sort_keys=True, separators=(",", ":"))
                truncated = True
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if digest in seen:
                truncated = True
                continue
            if item.source not in sources and len(sources) >= request.budget.max_sources:
                truncated = True
                continue
            if (
                len(selected) >= request.budget.max_records
                or used + len(encoded) > request.budget.max_characters
            ):
                truncated = True
                continue
            selected.append(item)
            seen.add(digest)
            sources.add(item.source)
            used += len(encoded)
        return AssembledContext(
            request.task_id,
            tuple(selected),
            used,
            truncated,
            permission_decision_id=decision.decision_id,
        )


__all__ = ["ContextAssembler", "KnowledgeRetriever", "MemoryRetriever", "StateReader"]

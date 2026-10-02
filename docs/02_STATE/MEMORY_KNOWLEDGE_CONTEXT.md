# MEMORY / KNOWLEDGE / STATE / CONTEXT

Phase 7 implements four separate local subsystems. They share a scope contract and the existing Phase 4 permission evaluator, but they do not share authority or silently convert records between subsystems.

## Memory

Memory is retained historical information, not current live State or reference Knowledge. A durable memory contains a stable identity and logical key, scope, explicit layer/type, FACT or INFERENCE assertion type, bounded structured content, source/evidence provenance, temporal validity, confidence, lifecycle, retention, privacy, revision, and correction linkage.

Implemented layers are Working, Episodic, Semantic, Procedural, Project, Core, and Archive. These labels establish explicit semantics; they do not claim sophisticated cognitive consolidation. Writes require explicit write intent and reason. Conversation turns, model output, events, and context are not automatically persisted.

Ordinary retrieval is permission- and exact-scope-filtered, lexical, bounded, and returns only active records that are currently temporally valid and unexpired. Restricted records require an additional scoped capability. FACT outranks INFERENCE when lexical relevance is otherwise equal. Provenance is returned with every record.

Default retention is:

- Task, Workflow, and Agent scope: 7 days.
- Project, Domain, and System scope: 90 days.
- User scope and Preference, Decision, or Relationship types: owner-controlled.

Expiration excludes a record from ordinary retrieval; it does not claim physical deletion. Correction creates a new revision and marks the old assertion Superseded or Contradicted. A stale correction is rejected. Forgetting marks the full logical revision chain Forgotten and scrubs its content in SQLite, leaving only minimal identifiers/provenance metadata required to identify the lifecycle operation. Forgotten content is unavailable to ordinary retrieval and Context Assembly.

## State

State is the current live snapshot only. It is not historical Memory and does not replace canonical Task or domain aggregates.

Each configured state category has exactly one subsystem owner identifier. A caller cannot create an arbitrary owner/category pair. Only that owner can create or compare-and-set a snapshot. Updates require the current revision and atomically increment it; stale updates do not overwrite newer state. Reads require the existing scoped `state.read` permission and return only the current record. The store intentionally keeps no generic history API.

Phase 7 does not migrate canonical Task state or Freelancing lead state into this store. Those existing owners remain authoritative; the generic State store is available for explicitly configured current resource snapshots.

## Knowledge

Knowledge is ingested reference material, not retained experience. Controlled ingestion validates a source identity, source type/reference, exact scope, bounded content, source version, and non-secret metadata. Content is deterministically divided into chunks of at most 2,000 characters and persisted with source provenance and ingestion time.

A duplicate source/scope/version/content ingestion is idempotent. Different content under an existing source version is a conflict. Ingesting a new version preserves old chunks as Superseded and makes the new chunks Current. Ordinary permission-filtered retrieval selects current versions only unless a caller explicitly requests version history, and supports source filtering, lexical relevance, result count, and character budgets. No semantic/vector-search capability is claimed.

## Context Assembly

Context is a disposable task-specific view and has no database. An assembly request identifies the requester, Task, exact scope, query, current user instruction, optional task data, explicit State references, source flags, and record/character/source budgets.

The assembler first authorizes `context.assemble`, then obtains each source through that source's own authorized API. Deterministic precedence is:

1. current user instruction;
2. supplied canonical task data;
3. requested current authoritative State;
4. verified-outcome Memory;
5. factual Memory;
6. current Knowledge;
7. inference Memory.

Lexical relevance orders records within precedence. Identical content is deduplicated. Hard source, record, and character budgets are applied deterministically; the current instruction is truncated rather than silently replaced when it alone exceeds the character budget. Every item identifies its source subsystem, source record, scope, version/revision, provenance, and selection reason. Assembly does not persist context or create Memory, State, Knowledge, permission, or approval.

Agent Runtime can inject a Context Assembler through a narrow provider protocol. `ExecutionContext.request_context` binds requester and task identity and does not expose stores to handlers.

## Security and limitations

Memory, Knowledge, and Context never grant permission or approval. State ownership is not inferred from content. Payload data, model output, and retrieved text remain untrusted. Bounded structured validation rejects secret-shaped keys/assignments, unsupported values, non-finite numbers, and oversized records.

All stores are synchronous, local, and single-process SQLite implementations. Retrieval is deterministic lexical matching, not embeddings or semantic reasoning. There is no automatic consolidation, background expiry deletion, cross-scope inference, identity rewriting, distributed coordination, cache, scheduler, workflow engine, or Phase 8 recovery/observability/resource platform.

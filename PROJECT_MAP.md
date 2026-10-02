# ZYRO Project Map

## Purpose

ZYRO is designed as one canonical personal Executive backed by replaceable models, bounded tools, explicit orchestration, distinct memory/state/knowledge layers, policy-controlled execution, and independent verification. Current code implements a runnable authenticated localhost product over the Executive/Task/Agent runtime, durable Workflow system, model/tool routing, scoped authorization and approval, trusted verifier evidence, Resource-enforced local execution, provider-neutral encrypted integrations/OAuth, durable communication, Memory/State/Knowledge/Context, Recovery/Observability, and a bounded Freelancing loop. The responsive UI uses the canonical Application/API boundary and never duplicates business authority. Real credentialed providers and production deployment operations remain not configured; version 1.0 readiness is not claimed.

## Repository structure

| Path | Responsibility |
| --- | --- |
| `src/zyro/` | Installable Python package and implementation source |
| `src/zyro/api/` | Authenticated FastAPI boundary plus built responsive local frontend assets |
| `src/zyro/application/` | Canonical product composition, chat/Task projections, and UI-independent service |
| `src/zyro/workflows/` | Minimal durable DAG Workflow state, controls, execution, history, and local triggers |
| `src/zyro/integrations/` | Provider-neutral OAuth, connections, accounts, scopes, health, and encrypted credentials |
| `src/zyro/persistence/` | Forward-only migration, integrity, and backup primitives |
| `src/zyro/core/` | Canonical Executive, Task lifecycle, errors/risk/verification contracts, Event envelope and compatibility publisher, configuration, and logging |
| `src/zyro/communication/` | Authorized Direct Messages plus SQLite Event persistence, subscriber delivery, ACK/retry/order/dead-letter state, and recovery |
| `src/zyro/memory/` | Scoped historical assertions, provenance, retention, correction/contradiction, forgetting, privacy, and bounded retrieval |
| `src/zyro/state/` | Durable current snapshots, configured category ownership, authorized reads, and compare-and-set revisions |
| `src/zyro/knowledge/` | Controlled reference ingestion, deterministic chunks, source versions, provenance, and bounded retrieval |
| `src/zyro/context/` | Transient permission-filtered source selection, precedence, deduplication, provenance, and context budgets |
| `src/zyro/recovery/` | Structured failures, deterministic recovery policy, durable operation/decision reconciliation, canonical Task retry adapter, and recovery events |
| `src/zyro/observability/` | Durable bounded redacted execution traces, indexed queries, and fail-open runtime/model/tool observation adapters |
| `src/zyro/resources/` | Configurable token/concurrency/rate controls, fair queues, expiring leases, durable usage/hard stops, and narrow Recovery integration |
| `src/zyro/agents/` | Agent Definition, Agent Instance, bounded handler contract, and in-process registry |
| `src/zyro/runtime/` | Process bootstrap plus Agent Runtime with narrow model/tool invocation boundaries |
| `src/zyro/models/` | Provider-independent definitions, requirements, results, registries, deterministic router, bounded fallback, and encrypted-key Gemini REST adapter |
| `src/zyro/tools/` | Bounded definitions, calls/results, handlers, in-process registry, validation, and executor |
| `src/zyro/execution/` | Independent verification protocol and basic structural runtime verifier |
| `src/zyro/security/` | Scoped permission, risk-path policy, action-bound approval, and tool authorization composition |
| `src/zyro/domains/freelancing/` | Qualification plus immutable outreach, authorized idempotent channel execution, normalized replies, revisioned projects, resource-gated canonical delivery Tasks, QA, and handoff |
| `src/zyro/interfaces/` | Explicit future interface boundary; not yet implemented |
| `tests/unit/` | Isolated component behavior and failure cases |
| `tests/integration/` | Behavior across package boundaries and successful local lifecycle checks |
| `tests/architecture/` | Executable ownership/invariant and forbidden-scope guards |
| `tests/security/` | Hostile-input and exact authority/approval boundary checks |
| `tests/restart/` | Durable idempotency, crash, terminal-state, and reconciliation checks |
| `tests/e2e/` | Complete failure-path lifecycle checks complementing successful integration E2E |
| `config/` | Non-secret, reviewable configuration defaults/examples |
| `docs/00_MASTER/` | Product and architecture authority |
| `docs/01_CONTRACTS/` | Core data/interaction contracts |
| `docs/02_STATE/` | Memory, knowledge, state, and context policy |
| `docs/03_SECURITY/` | Security, permission, and approval policy |
| `docs/04_RUNTIME/` | Resource, recovery, and observability policy |
| `docs/05_DEVELOPMENT/` | Roadmap, development protocol, testing, and change management |
| `domains/` | Domain specifications; domain code must not duplicate core concerns |
| `PROGRESS.md` | Append-only record of actual implementation and verification status |

## Architecture layers

The intended flow is Interface → Executive/Identity/Brain → Orchestration and Runtime → Resource and State services → Tools/Execution → Permission/Approval/Security → Real World → Observability/Recovery. Implemented Core flow remains Executive → Task → Agent Definition/Instance → Agent Runtime → optional model/tool/context boundaries → independent verification → guarded Task completion. The Context Assembler gives an agent a bounded transient view rather than store access. It ranks current instruction and task data before current State, verified/factual Memory, inference Memory, and current Knowledge while every underlying read enforces scope and Phase 4 permission. Memory, State, and Knowledge use separate schemas and ownership rules. Communication remains transport and never owns Task or lead state. Resource controls gate capacity but never schedule or authorize work; Recovery produces safe decisions but never executes them; Observability records redacted operational facts but never mutates Task, State, or Memory. Mandatory distinctions and safety constraints are in `docs/00_MASTER/ARCHITECTURE_INVARIANTS.md`.

## Authority and source-of-truth rules

1. `docs/00_MASTER/` defines product intent and non-negotiable architecture invariants.
2. Contracts live in `docs/01_CONTRACTS/`; policies live in `docs/02_STATE/`, `docs/03_SECURITY/`, and `docs/04_RUNTIME/`.
3. Domain specifications live under `domains/`; they cannot override core contracts or security policy.
4. Python implementation lives only under `src/zyro/`; executable evidence lives under `tests/`.
5. Implementation status comes from source, tests, and the chronological `PROGRESS.md`, never from documentation alone.
6. When documentation and implementation differ, report the discrepancy. Architecture changes follow `docs/05_DEVELOPMENT/CHANGE_MANAGEMENT.md`.
7. Secrets never belong in Git, ordinary configuration, logs, memory, or test fixtures.

## Phase mapping

The delivery sequence labels the scaffold **Phase 1 — Foundation** and Executive/Task/Agent work **Phase 2 — Executive Core + Task + Agent Runtime**. The existing `IMPLEMENTATION_ROADMAP.md` labels equivalent work Phase 0 and Phase 1 respectively. This naming difference does not authorize skipping roadmap capabilities.

- **Delivery Phase 1 / Roadmap Phase 0:** package, configuration, logging, tests, tooling, and architecture boundaries — implemented in this milestone.
- **Delivery Phase 2 / Roadmap Phase 1:** Executive core, Task, Agent contracts/runtime, finite retry, and basic structural verification — implemented and verified.
- **Delivery Phase 3 / Roadmap Phase 2:** Model Router + Tool System — implemented and verified.
- **Delivery Phase 4 / Roadmap Phase 3:** Permission + Approval + Verification — implemented and verified.
- **Delivery Phase 5 / Roadmap Phase 5:** Freelancing Qualification + Scoring — implemented and verified as a bounded consumer slice. No lead-finding integration or outreach was added.
- **Delivery Phase 6 / Roadmap Phase 6:** Communication + Durable Event Bus — implemented with local SQLite persistence, bounded synchronous delivery, and Phase 4 authorization reuse.
- **Delivery Phase 7 / Roadmap Phase 7:** Memory + State + Knowledge + Context — implemented with separate local SQLite stores, deterministic bounded retrieval, and transient Context Assembly.
- **Delivery Phase 8:** Recovery + Observability + Resource Hardening — implemented with deterministic non-authoritative recovery, redacted SQLite traces, and local durable limits/leases/accounting.
- **Combined Phase 9 + Phase 10 Round 1:** Outreach + External Actions + Client Replies + Delivery + QA + Handoff — implemented as a bounded local Freelancing lifecycle using existing authority/runtime services and adapter-only external channels.
- **Final Round 2:** E2E Hardening + System Integration + Production-readiness Assessment — implemented and verified for the local architecture. Version 1.0 readiness remains explicitly unmet for the limitations recorded in `docs/05_DEVELOPMENT/PRODUCTION_READINESS.md`.
- **Next major round:** None authorized. Any production integration must be separately scoped and reviewed; no feature phase is implied.

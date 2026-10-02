# ZYRO

ZYRO is specified as a personal Executive AI, computer agent, and AI organization. The user will interact with one canonical Executive while internal components coordinate domains, agents, models, tools, workflows, state, knowledge, security, execution, and verification.

This repository has completed the **Final Round 2 — E2E Hardening, System Integration, and Production-readiness Assessment** at version **0.10.0**. The controlled local freelancing lifecycle and prior Executive/Task/Agent, authorization, durable communication, data, recovery, observability, and resource foundations have code-level architecture guards, adversarial authority tests, success/failure E2E evidence, and restart/idempotency hardening. Exact outreach approvals now also bind requester/request/Task/workflow context; identical approval callbacks and verified evidence are idempotent while conflicts fail closed. Version 1.0 readiness is **not met**: external channels remain adapter-only, the bundled channel is explicitly simulated, Permission/Approval are process-local, and no formal database migration/deployment/provider operations platform exists. The repository still does **not** include a scheduler, workflow engine, browser/computer control, UI, payments, distributed deployment, or autonomous outreach.

> Naming note: historical delivery phases 1–4 were offset by one from roadmap phases 0–3. `PROGRESS.md` records actual implementation status and phase mapping.

## Requirements

- Python 3.11 or newer
- `pip`
- No API key, external database, container, network access, or external service is needed for development/tests

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the foundation package:

```bash
python -m zyro
```

The command initializes safe configuration and logging, then exits. It does not contact an AI provider. Application code composes `ZyroExecutive` from an `AgentRegistry`, `AgentRuntime`, bounded `AgentHandler` implementations, and an optional independent `Verifier`. Without a verifier, successful execution is explicitly reported as `SUCCEEDED_UNVERIFIED`; it is never promoted to `DONE` or `VERIFIED`.

Core contracts are located at:

- `src/zyro/core/` — Executive, Task lifecycle, shared errors/risk/verification contracts, configuration, and logging
- `src/zyro/security/` — scoped permission, risk-path policy, action-bound approval, and tool authorization composition
- `src/zyro/agents/` — definitions, instances, handlers, and registry
- `src/zyro/models/` — model requirements/definitions/results, provider registry, model registry, and deterministic router
- `src/zyro/tools/` — bounded definitions/calls/results, registry, schema validation, and executor
- `src/zyro/runtime/agent_runtime.py` — one bounded agent attempt with optional model/tool service boundaries
- `src/zyro/execution/verification.py` — independent structural verification protocol
- `src/zyro/domains/freelancing/` — qualification plus exact-message outreach, idempotent channel adapters, reply intake/processing, project state, canonical delivery Tasks, QA, and handoff
- `src/zyro/communication/` — authorized Direct Message delivery and the local durable SQLite Event Bus
- `src/zyro/memory/` — selective historical records, provenance, retention, correction, forgetting, and bounded retrieval
- `src/zyro/state/` — owner-controlled current snapshots with compare-and-set revisions
- `src/zyro/knowledge/` — controlled reference ingestion, deterministic chunking, source versions, and retrieval
- `src/zyro/context/` — permission-filtered transient assembly with provenance, precedence, deduplication, and budgets
- `src/zyro/recovery/` — failure taxonomy, deterministic bounded decisions, durable operation/reconciliation state, and Task/Event adapters
- `src/zyro/observability/` — redacted durable operational traces, bounded queries, and fail-open runtime/model/tool adapters
- `src/zyro/resources/` — token accounting, hard stops, concurrency queues, leases, lanes, rate limits, and the narrow Recovery bridge
- `src/zyro/core/events.py` — canonical Event envelope plus the compatibility in-process publisher

Providers and tools are registered programmatically with non-secret definitions. The repository ships no product provider adapter and requires no API key. Deterministic providers, tools, and lead fixtures under `tests/` are test infrastructure only. Model-requested tool calls are inert data: every supported tool execution passes through a required, separate authorizer before the handler. Capability, risk classification, standing permission, action approval, execution, verification, and Task completion remain distinct.

Freelancing qualification/scoring is pure policy evaluation and does not invoke a tool or grant authority. Each stage is independently reproduced against the same authoritative lead revision and policy before a compare-and-set state write. A score remains business output only. `LEAD_QUALIFIED` is durably persisted only after the final verified state commit. Lead state and event persistence are not one atomic transaction; an explicit idempotent reconciliation method repairs a reported publication failure without making the Event Bus authoritative for lead state.

Operational Freelancing extends that verified lead with immutable outreach preparations containing the exact recipient/channel/message, evidence, policy, and verification plan. `send_outreach` is always `STRICT_AUTHORIZATION`: the existing permission evaluator runs at dispatch, and the existing expiring Approval service binds exact arguments plus a safe human-readable review display. The only bundled outbound adapter is explicitly simulated. Durable action identities prevent duplicate dispatch, interrupted `DISPATCHING` work reopens as `UNCERTAIN`, and Phase 8 Recovery permits reconciliation—not blind resend. Provider acceptance, delivery, and independent verification remain distinct.

Validated external replies are persisted and published as data before deterministic advisory classification; reply text has no tool, permission, approval, contract, or project authority. Potential projects begin `PROJECT_PENDING` and require explicit compare-and-set activation. Delivery work runs through canonical Executive/Task/Agent/Verification under Resource reservations. QA records criteria/evidence and can fail or remain inconclusive. Handoff reaches verified completion only when canonical delivery Tasks, deliverables, and QA evidence are all verified.

The Event Bus provides **at-least-once** local delivery, not exactly-once execution and not global ordering. Ordering applies only per subscriber and configured ordering key. Handler bindings are process-local and must be rebound after restart; persisted unknown subscriptions remain pending. Direct Message handler timeout detection is cooperative for synchronous handlers. Both communication paths reuse Phase 4 permission evaluation and fail closed before delivery.

Memory, State, Knowledge, and Context remain separate authorities. Memory stores explicit historical assertions—not every interaction—and distinguishes fact from inference, provenance, scope, privacy, retention, supersession, contradiction, and forgetting. State stores only current snapshots under a configured subsystem owner and rejects stale compare-and-set writes. Knowledge stores controlled source/version chunks and normally retrieves only the current version. Context persists nothing: it combines explicit user/task input with permitted current State, effective Memory, and current Knowledge using deterministic lexical relevance, source precedence, deduplication, and hard record/character/source budgets. None of these records grant permission or approval.

## Quality checks

```bash
pytest
ruff check .
ruff format --check .
mypy
```

Tests are separated into `tests/unit`, `tests/integration`, and `tests/architecture`.

## Configuration and secrets

Safe defaults need no local configuration. ZYRO reads these optional process environment variables:

- `ZYRO_ENV` (default: `development`)
- `ZYRO_LOG_LEVEL` (default: `INFO`)
- `ZYRO_TASK_TOKEN_LIMIT` (default: `50000`)
- `ZYRO_WORKFLOW_TOKEN_LIMIT` (default: `300000`)
- `ZYRO_MAX_CONCURRENT_TASKS` (default: `8`)
- `ZYRO_MAX_CONCURRENT_AGENTS` (default: `8`)
- `ZYRO_MAX_CONCURRENT_TOOL_CALLS` (default: `4`)

`config/default.toml` documents equivalent non-secret settings. Code can explicitly pass a TOML path to `initialize_runtime`; local overrides should use ignored `config/local.toml`. The package intentionally does not auto-load `.env`, but `.env` and common secret paths are ignored for developer tooling. Never commit credentials or put secrets in config files, source, logs, or ordinary memory.

## Repository guide

- `src/zyro/` — Python source and subsystem boundaries
- `tests/` — executable unit, integration, and architecture checks
- `config/` — non-secret configuration examples/defaults
- `docs/` — product, architecture, contract, policy, and development sources
- `domains/` — domain specifications
- `src/zyro/domains/` — implemented bounded domain consumers of Core
- `PROJECT_MAP.md` — directory ownership, architecture layers, and source-of-truth rules
- `PROGRESS.md` — append-only implementation and verification history
- `docs/05_DEVELOPMENT/PRODUCTION_READINESS.md` — factual implemented/partial/adapter-only limitations and the 1.0 readiness decision

Start with `START_HERE.md`, then read the master specification and invariants in `docs/00_MASTER/`. Before making implementation changes, read `PROGRESS.md` and `PROJECT_MAP.md`.

## Status discipline

Documentation describes intended behavior; source and tests show what exists. `PROGRESS.md` must be updated after verified work and must retain prior entries. Do not label a documented capability as implemented, tested, or verified without corresponding code and evidence.

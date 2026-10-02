# ZYRO

ZYRO is specified as a personal Executive AI, computer agent, and AI organization. The user will interact with one canonical Executive while internal components coordinate domains, agents, models, tools, workflows, state, knowledge, security, execution, and verification.

This repository has completed **Phase 7 — Memory + State + Knowledge + Context** in the delivery sequence. It implements the prior Executive/Task/Agent, model/tool, authorization, Freelancing, and durable communication phases plus selective historical Memory, owner-controlled current State, versioned reference Knowledge, and transient bounded Context Assembly. Phase 7 uses local SQLite persistence and deterministic lexical retrieval; it does **not** include vector search, external storage, a workflow engine, scheduler, full recovery/observability/resource management, outreach, email, CRM, browser control, or later delivery-domain behavior.

> Naming note: historical delivery phases 1–4 were offset by one from roadmap phases 0–3. Phase 7 corresponds to the memory/state/knowledge/context roadmap milestone; `PROGRESS.md` records actual implementation status.

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
- `src/zyro/domains/freelancing/` — lead contracts, policies, evaluators, Core agents, revisioned state, verification, and qualification pipeline
- `src/zyro/communication/` — authorized Direct Message delivery and the local durable SQLite Event Bus
- `src/zyro/memory/` — selective historical records, provenance, retention, correction, forgetting, and bounded retrieval
- `src/zyro/state/` — owner-controlled current snapshots with compare-and-set revisions
- `src/zyro/knowledge/` — controlled reference ingestion, deterministic chunking, source versions, and retrieval
- `src/zyro/context/` — permission-filtered transient assembly with provenance, precedence, deduplication, and budgets
- `src/zyro/core/events.py` — canonical Event envelope plus the compatibility in-process publisher

Providers and tools are registered programmatically with non-secret definitions. The repository ships no product provider adapter and requires no API key. Deterministic providers, tools, and lead fixtures under `tests/` are test infrastructure only. Model-requested tool calls are inert data: every supported tool execution passes through a required, separate authorizer before the handler. Capability, risk classification, standing permission, action approval, execution, verification, and Task completion remain distinct.

Freelancing qualification/scoring is pure policy evaluation and does not invoke a tool or grant authority. Each stage is independently reproduced against the same authoritative lead revision and policy before a compare-and-set state write. A score remains business output only. `LEAD_QUALIFIED` is durably persisted only after the final verified state commit. Lead state and event persistence are not one atomic transaction; an explicit idempotent reconciliation method repairs a reported publication failure without making the Event Bus authoritative for lead state.

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

Start with `START_HERE.md`, then read the master specification and invariants in `docs/00_MASTER/`. Before making implementation changes, read `PROGRESS.md` and `PROJECT_MAP.md`.

## Status discipline

Documentation describes intended behavior; source and tests show what exists. `PROGRESS.md` must be updated after verified work and must retain prior entries. Do not label a documented capability as implemented, tested, or verified without corresponding code and evidence.

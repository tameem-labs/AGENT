# ZYRO

ZYRO is specified as a personal Executive AI, computer agent, and AI organization. The user will interact with one canonical Executive while internal components coordinate domains, agents, models, tools, workflows, state, knowledge, security, execution, and verification.

This repository has completed **Phase 4 — Permission + Approval + Verification** in the delivery sequence. It implements the Executive/Task/Agent runtime, provider-independent model routing, bounded tools, scoped standing permission, action-bound human approval state, mandatory pre-handler authorization, approval waiting, honest unknown outcomes, and structured verification evidence. It does **not** include a real external model adapter, approval UI, durable/distributed authorization, advanced semantic verification, memory, workflows, or domain business logic.

> Naming note: the existing architecture roadmap calls Permission + Approval + Verification “Phase 3,” while the delivery sequence calls it “Phase 4.” See `PROGRESS.md` for actual implementation status.

## Requirements

- Python 3.11 or newer
- `pip`
- No API key, database, container, network access, or external service is needed for development/tests

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

Providers and tools are registered programmatically with non-secret definitions. The repository ships no product provider adapter and requires no API key. Deterministic providers and tools under `tests/` are test infrastructure only. Model-requested tool calls are inert data: every supported tool execution passes through a required, separate authorizer before the handler. Capability, risk classification, standing permission, action approval, execution, verification, and Task completion remain distinct. Missing or stale authority fails closed; approval-required actions return a bound pending approval and do not invoke the handler. Structural verification proves only runtime consistency, never semantic or real-world correctness.

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
- `domains/` — domain specifications; no domain implementation yet
- `PROJECT_MAP.md` — directory ownership, architecture layers, and source-of-truth rules
- `PROGRESS.md` — append-only implementation and verification history

Start with `START_HERE.md`, then read the master specification and invariants in `docs/00_MASTER/`. Before making implementation changes, read `PROGRESS.md` and `PROJECT_MAP.md`.

## Status discipline

Documentation describes intended behavior; source and tests show what exists. `PROGRESS.md` must be updated after verified work and must retain prior entries. Do not label a documented capability as implemented, tested, or verified without corresponding code and evidence.

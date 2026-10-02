# ZYRO

ZYRO is specified as a personal Executive AI, computer agent, and AI organization. The user will interact with one canonical Executive while internal components coordinate domains, agents, models, tools, workflows, state, knowledge, security, execution, and verification.

This repository has completed **Phase 2 — Executive Core + Task + Agent Runtime** in the delivery sequence. It implements one orchestration entry point, guarded Task lifecycles, separate Agent Definition and Agent Instance contracts, bounded agent handlers, structured runtime outcomes, finite retries, and independent basic verification. It does **not** implement model providers, tools, external APIs, memory, or domain business logic.

> Naming note: the existing architecture roadmap calls this Executive/Task/Agent work “Phase 1,” while the delivery sequence calls it “Phase 2.” The next delivery milestone, “Phase 3 — Model Router + Tool System,” corresponds to roadmap Phase 2. See `PROGRESS.md` for actual implementation status.

## Requirements

- Python 3.11 or newer
- `pip`
- No API key, database, container, or external service is needed for the foundation

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

Phase 2 contracts are located at:

- `src/zyro/core/task.py` — Task lifecycle and verification records
- `src/zyro/core/executive.py` — canonical request/orchestration boundary
- `src/zyro/agents/` — definitions, instances, handlers, and registry
- `src/zyro/runtime/agent_runtime.py` — one bounded execution attempt
- `src/zyro/execution/verification.py` — independent verification protocol

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

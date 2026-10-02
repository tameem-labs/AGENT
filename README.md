# ZYRO

ZYRO is specified as a personal Executive AI, computer agent, and AI organization. The user will interact with one canonical Executive while internal components coordinate domains, agents, models, tools, workflows, state, knowledge, security, execution, and verification.

This repository is currently at **Phase 1 — Foundation** in the delivery sequence. It provides only the Python package, safe configuration, structured logging, test layers, and architecture boundaries needed for later work. It does **not** implement the Executive, agent behavior, model providers, tools, or AI responses.

> Naming note: the existing architecture roadmap calls the repository/runtime scaffold “Phase 0.” The current delivery milestone calls that work “Phase 1 — Foundation”; its next milestone, “Phase 2 — Executive Core + Task + Agent Runtime,” corresponds to roadmap Phase 1. See `PROGRESS.md` for actual implementation status.

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

The command initializes safe configuration and logging, then exits. It does not contact an AI provider.

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

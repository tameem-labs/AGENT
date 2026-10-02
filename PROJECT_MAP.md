# ZYRO Project Map

## Purpose

ZYRO is designed as one canonical personal Executive backed by replaceable models, bounded tools, explicit orchestration, distinct memory/state/knowledge layers, policy-controlled execution, and independent verification. Current code implements the Executive/Task/Agent runtime plus provider-independent model routing and bounded tool infrastructure; later documented capabilities remain specifications rather than implementations.

## Repository structure

| Path | Responsibility |
| --- | --- |
| `src/zyro/` | Installable Python package and implementation source |
| `src/zyro/core/` | Canonical Executive, Task lifecycle, errors, configuration, and logging |
| `src/zyro/agents/` | Agent Definition, Agent Instance, bounded handler contract, and in-process registry |
| `src/zyro/runtime/` | Process bootstrap plus Agent Runtime with narrow model/tool invocation boundaries |
| `src/zyro/models/` | Provider-independent definitions, requirements, results, registries, provider contract, and deterministic router |
| `src/zyro/tools/` | Bounded definitions, calls/results, handlers, in-process registry, validation, and executor |
| `src/zyro/execution/` | Independent verification contract and basic structural runtime verifier |
| `src/zyro/{memory,knowledge,state,security,interfaces}/` | Explicit future subsystem boundaries; not yet implemented |
| `tests/unit/` | Isolated component behavior and failure cases |
| `tests/integration/` | Behavior across package boundaries and runtime smoke checks |
| `tests/architecture/` | Lightweight checks for required boundaries and source documents |
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

The intended flow is Interface → Executive/Identity/Brain → Orchestration and Runtime → Resource and State services → Tools/Execution → Permission/Approval/Security → Real World → Observability/Recovery. Implemented flow is Executive → Task → Agent Definition/Instance → Agent Runtime → optional Model Router/Provider and bounded Tool Executor → independent basic verification. Model suggestions never authorize tool execution, and policy-controlled tools fail closed until the next phase. Empty package boundaries are not claims that later layers are implemented. Mandatory distinctions and safety constraints are in `docs/00_MASTER/ARCHITECTURE_INVARIANTS.md`.

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
- **Delivery Phase 4 / Roadmap Phase 3:** Permission + Approval + Verification — next permitted work; not implemented.
- **Later roadmap phases:** domains, communication, state systems, hardening, and end-to-end workflows — documented only until their phase begins.

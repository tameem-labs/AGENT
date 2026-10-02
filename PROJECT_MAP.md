# ZYRO Project Map

## Purpose

ZYRO is designed as one canonical personal Executive backed by replaceable models, bounded tools, explicit orchestration, distinct memory/state/knowledge layers, policy-controlled execution, and independent verification. The current code is only the engineering foundation; documented future capabilities are not implementations.

## Repository structure

| Path | Responsibility |
| --- | --- |
| `src/zyro/` | Installable Python package and implementation source |
| `src/zyro/core/` | Cross-cutting foundations such as configuration and logging |
| `src/zyro/runtime/` | Process bootstrap now; later runtime composition belongs here |
| `src/zyro/{agents,memory,knowledge,state,tools,execution,security,interfaces}/` | Explicit subsystem boundaries reserved for their named concerns |
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

The intended flow is Interface → Executive/Identity/Brain → Orchestration and Runtime → Resource and State services → Tools/Execution → Permission/Approval/Security → Real World → Observability/Recovery. Empty package boundaries are not claims that those layers are implemented. Mandatory distinctions and safety constraints are in `docs/00_MASTER/ARCHITECTURE_INVARIANTS.md`.

## Authority and source-of-truth rules

1. `docs/00_MASTER/` defines product intent and non-negotiable architecture invariants.
2. Contracts live in `docs/01_CONTRACTS/`; policies live in `docs/02_STATE/`, `docs/03_SECURITY/`, and `docs/04_RUNTIME/`.
3. Domain specifications live under `domains/`; they cannot override core contracts or security policy.
4. Python implementation lives only under `src/zyro/`; executable evidence lives under `tests/`.
5. Implementation status comes from source, tests, and the chronological `PROGRESS.md`, never from documentation alone.
6. When documentation and implementation differ, report the discrepancy. Architecture changes follow `docs/05_DEVELOPMENT/CHANGE_MANAGEMENT.md`.
7. Secrets never belong in Git, ordinary configuration, logs, memory, or test fixtures.

## Phase mapping

The delivery sequence used for the active build labels the scaffold **Phase 1 — Foundation** and the next work **Phase 2 — Executive Core + Task + Agent Runtime**. The existing `IMPLEMENTATION_ROADMAP.md` labels equivalent work Phase 0 and Phase 1 respectively. This naming difference does not authorize skipping roadmap capabilities.

- **Delivery Phase 1 / Roadmap Phase 0:** package, configuration, logging, tests, tooling, and architecture boundaries — implemented in this milestone.
- **Delivery Phase 2 / Roadmap Phase 1:** Executive core, Task, and Agent runtime — next permitted work; not implemented.
- **Later roadmap phases:** Model Router, tools, permission/approval/verification, domains, communication, state systems, hardening, and end-to-end workflows — documented only until their phase begins.

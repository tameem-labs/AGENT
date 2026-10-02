# ZYRO

ZYRO 0.12.0 is a runnable local-first Personal Executive AI product. It exposes an authenticated localhost interface backed by the same canonical Executive, Task, Agent, Model Router, Workflow, Resource, Verification, Recovery, and domain contracts used by the library.

Google Gemini is the default configurable AI provider and is invoked through the canonical Model Router when its key validates. The deterministic local provider remains an explicitly **SIMULATED** fallback. Google account OAuth, GitHub, Instagram, real email/CRM, browser control, and voice still require reviewed provider configuration and are shown as **NOT CONFIGURED** or **UNAVAILABLE**—never as working integrations.

## Start locally

Requirements: Python 3.11+ and `pip`.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
zyro serve
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
zyro serve
```

Windows Command Prompt:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
zyro serve
```

Open **http://127.0.0.1:8000**. The startup message reports only safe owner/provider state and the local data directory.

### First run

1. Create a 12+ character local-owner password.
2. Enter a Gemini API key and select **Save & test**. Obtain a key only from the linked official Google AI Studio page: `https://aistudio.google.com/app/apikey`.
3. If Gemini cannot be configured yet, explicitly choose the labeled simulated local provider.
4. Finish setup and optionally open Integration Center.
5. Start a conversation.

Gemini keys are validated server-side, encrypted with AES-GCM, and never returned by an API or placed in browser storage, Tasks, Workflows, chat, Memory, or logs. Settings supports validation, replacement, and removal. The owner password is scrypt-hashed; session tokens are hashed; mutations require CSRF and same-origin checks.

Useful commands:

```bash
zyro serve --host 127.0.0.1 --port 8000 --data-dir .zyro
zyro check
zyro backup backups/local
pytest -q
ruff check .
ruff format --check .
mypy
```

For an Arena/live-preview host, bind explicitly with `zyro serve --host 0.0.0.0`.

## Actual local product

- **Executive chat:** every message creates a durable Workflow and runs a canonical Executive → Task → Agent → Model Router → configured Gemini (or explicit simulated fallback) → Resource accounting → independent structural Verification path.
- **First-run provider setup:** configure, validate, replace, or remove Gemini from the UI without exposing its key; invalid credentials are not persisted.
- **Active work and Task detail:** real Task projections show status, agent, model, workflow, attempts, resource limits, verification, errors, and result.
- **Workflows:** durable DAG steps, dependencies, attempts, waiting, approval-required state, retry, pause, resume, cancellation, completion, event/schedule contracts, and restart recovery.
- **Agents:** real Executive and domain capability metadata without secrets.
- **Approval Center:** reserved for canonical backend Approval state; no frontend-only approval authority exists.
- **Memory and Activity:** honest canonical state views and correlated Workflow history. Credentials never enter Memory.
- **Integration Center:** provider-neutral definitions, multiple account records, server-side OAuth state and PKCE, callback exchange, encrypted local token vault, disconnect/revoke, scopes, health, and connected account display.
- **System Status and Settings:** actual local runtime, provider, Workflow, Task, integration, voice, and browser availability.
- **Responsive dark frontend:** static product assets are served by the authenticated FastAPI application; the browser never accesses SQLite directly.

## Safety changes in 0.12.0

- Trusted domain verification now requires verifier-issued, HMAC-authenticated evidence binding subject/action, Task, Workflow, source, reference, digest, verifier, method, result, timestamp, and trust class. Arbitrary strings and `passed=True` no longer create trusted outreach, deliverable, QA, or Handoff completion.
- Local authentication is separate from Permission and Approval. OAuth permission is also separate from ZYRO action authority.
- Short-lived one-use dispatch grants revalidate authority at claim immediately before handler execution. Once claimed, execution is explicitly considered started; no atomicity with an external provider is claimed.
- Model invocations are Resource-aware and enforce Task/Workflow accounting. Unknown successful provider usage fails conservatively rather than silently bypassing accounting.
- Configured generic Tool timeouts execute through a bounded daemon worker. If a blocking operation cannot be stopped, the outcome is `UNKNOWN`, not safe automatic retry.
- Verified Handoff persistence and project completion now share one SQLite transaction and durable completion operation.
- Delivery operations are persisted before Executive execution. Interrupted operations reopen as `UNCERTAIN` and are not blindly repeated.
- SQLite project, Workflow, identity, application, integration, Resource, and credential state survive local restart. `zyro check` and `zyro backup` provide integrity and backup operations.

## Integration and OAuth configuration

Normal connection management happens in the UI. The frontend never accepts or receives refresh tokens. Backend flow:

```text
UI → OAuth initiation → provider → callback → backend exchange
   → encrypted credential vault → scoped Integration Connection → bounded Tool
```

The development OAuth connector is explicitly simulated and useful for local testing. Google/GitHub/Instagram definitions are visible but not configured because this repository contains no provider client credentials. A real adapter must supply its OAuth endpoints and token exchange server-side. OAuth scopes do not grant ZYRO Tool Permission or action Approval.

Local credentials are AES-GCM encrypted with a randomly generated mode-0600 key under `ZYRO_DATA_DIR`. Do not commit `.zyro`, `.env`, provider secrets, databases, or backup files.

## Architecture map

- `src/zyro/api/` — authenticated FastAPI boundary and responsive frontend
- `src/zyro/application/` — UI-independent application composition and projections
- `src/zyro/workflows/` — minimal durable Workflow contracts, store, runner, and local triggers
- `src/zyro/security/` — local identity/session, Permission, Approval, dispatch grant, and authorization
- `src/zyro/integrations/` — definitions, connections, OAuth state, provider accounts, scopes, health, encrypted credentials
- `src/zyro/core/`, `agents/`, `runtime/` — Executive, canonical Task, Agent runtime
- `src/zyro/models/`, `tools/`, `resources/` — replaceable models, bounded tools, runtime accounting
- `src/zyro/execution/` — structural verification and trusted evidence authority
- `src/zyro/communication/`, `recovery/`, `observability/` — transport, safe decisions, and traces
- `src/zyro/memory/`, `state/`, `knowledge/`, `context/` — separate data authorities
- `src/zyro/domains/freelancing/` — controlled qualification, outreach, reply, delivery, QA, and Handoff

See `PROJECT_MAP.md`, `PROGRESS.md`, and `docs/05_DEVELOPMENT/PRODUCTION_READINESS.md` for exact ownership and limitations.

## Current limitations

ZYRO is a usable local product, not a production cloud deployment. Gemini is live only after the owner supplies and validates a key; availability, quota, regional access, and billing remain controlled by Google. No configured Google account OAuth, Gmail, Drive, Calendar, GitHub, Instagram, email/CRM, webhook, browser/computer, voice, payment, distributed worker, TLS termination, HA, or monitoring SaaS is bundled. The local fallback is deterministic and does not claim general reasoning or external research. Permission and Approval records outside the authenticated application composition remain process-local. Multi-process SQLite operation is not qualified. Version 1.0 production readiness is not claimed.

# Local product, OAuth, security, runtime, and test guide

## Startup and storage

Install with `python -m pip install -e ".[dev]"`, then run `zyro serve`. The default endpoint is `http://127.0.0.1:8000`; runtime data defaults to `.zyro/`. Use `--data-dir`, `--host`, and `--port` for an explicit location/bind. `zyro check` runs SQLite integrity checks and `zyro backup DESTINATION` creates consistent store backups. Clean shutdown closes the scheduler, application, Resource, Workflow, projection, Integration, and identity stores.

First launch presents local-owner setup. Subsequent access requires the password. Sessions use an HttpOnly SameSite=Strict cookie and a separate CSRF header for mutations. A browser Origin, when present, must match the API origin. Authentication identifies the principal; it does not create Permission, Approval, trusted evidence, or OAuth scope.

## Runtime path

The chat vertical path is:

```text
Browser → authenticated FastAPI → ZyroApplication → durable Workflow
→ ZyroExecutive → canonical Task → AgentRuntime → Resource-aware ModelRouter
→ configured Provider → structural Verifier → Task/Workflow projections → UI
```

Google Gemini is the default provider after the owner validates a key in first-run setup or Settings. The REST adapter retrieves the key only from the encrypted backend vault, uses an `x-goog-api-key` header, applies a bounded timeout, classifies credential/quota/network/context/model/response failures, reports provider usage to Resource accounting, and permits one safe bounded fallback for model-only work. `zyro.local-assistant` remains an explicitly SIMULATED deterministic fallback and does not claim internet research or external execution. Model/provider composition belongs to `models.router.build_model_router`; Application code does not own registries.

Task resource defaults are 50,000 tokens and Workflow defaults are 300,000. Unknown provider usage does not become zero. A Tool call passes registry validation, Tool Authorization, short-lived dispatch grant claim/revalidation, Resource admission where composed, handler timeout, result validation, verification hook, trace, and Recovery classification.

## OAuth and integrations

Definitions are non-secret provider metadata. Connections represent one provider account, label, scopes, health and status; multiple connection records can exist without source changes. OAuth initiation creates server-side expiring state and PKCE material. Callback consumes state once and performs exchange only through the backend provider adapter. Credentials are AES-GCM encrypted with a random local mode-0600 key. Access and refresh tokens are never API response fields and must never be copied into chat, Task data, Memory, Event payloads, ordinary logs, or configuration.

OAuth authorization only grants a provider scope. It never grants ZYRO Permission or Approval. An external action must independently pass both boundaries.

The development connector is SIMULATED and only validates the local flow. Google/GitHub/Instagram are NOT CONFIGURED. Adding one requires a backend `OAuthProvider`, secrets supplied outside source/config responses, bounded Tools, provider-specific error/timeout/idempotency semantics, and verifier-specific evidence.

## Verification and external effects

`VerificationAuthority` issues authenticated evidence bound to a `VerificationSubject`. It records source/reference/digest, verifier identity, timestamp, method, result, trust and claims. Ordinary caller strings, provider acceptance, model output, QA booleans, Events, traces, Memory, State, Knowledge, voice/camera content, and client content cannot produce trusted verification.

Dispatch grants narrow the Permission/Approval race: issuance and claim are bounded, one-use, fingerprint-bound, and claim rechecks authority. They cannot make an external API call atomic. Durable operation IDs are persisted before delivery; interrupted operations become UNCERTAIN and are reconciled instead of blindly replayed. Handoff’s verified local record and final project state are one SQLite transaction.

## Browser, filesystem, terminal, screen, and voice

No browser/computer control adapter is bundled. The UI truthfully reports UNAVAILABLE. Any future adapter must use narrow typed actions, explicit targets, canonical authorization, high-risk Approval, Resource bounds, timeouts, verification, traces, and uncertain-effect Recovery. It must not expose unrestricted shell/eval primitives, traverse outside explicit roots, infer approval from screen content, or treat camera/microphone input as authority.

Voice is a provider-neutral NOT CONFIGURED UI boundary. No recording, upload, transcription, or synthesis occurs in the current product.

## Validation

Run:

```bash
pytest -q
ruff check src tests
ruff format --check src tests
mypy src tests
python -m zyro
zyro check
```

Product tests cover static UI serving, owner setup/login, cookie/CSRF enforcement, actual chat-to-Task/Workflow/Resource execution, OAuth connect/disconnect and token ciphertext, forged evidence, dispatch revocation/one-use races, Workflow dependencies/restart, configured Tool timeout, domain restart reconciliation, and the full controlled freelancing E2E path. The suite does not assert that an unconfigured external provider works.

# Production-readiness assessment

Assessment date: 2026-10-02. Package version: 0.11.0.

This matrix reports demonstrated behavior. A passing local test does not imply that an unconfigured external provider exists.

| Area | Status | Demonstrated boundary | Remaining limitation |
| --- | --- | --- | --- |
| Local product UI/API | IMPLEMENTED | Authenticated responsive UI uses canonical Application API and real Task/Workflow state | Local FastAPI process; no production TLS/HA deployment |
| Core Task and Agent Runtime | IMPLEMENTED | Finite Task attempts, Agent instances, model/tool boundaries, independent verification | Canonical Task itself remains in-process; product projections and Workflows are durable |
| Workflow | IMPLEMENTED | Durable DAG, dependencies, attempts, controls, history, immediate/schedule/recurrence/event contracts, restart recovery | Scheduler is cooperative local polling, not a worker service |
| Local authentication | IMPLEMENTED | Scrypt owner password, hashed sessions, expiry/revocation, HttpOnly cookie, CSRF and origin checks | Single local owner; no passkeys, federation, or remote identity provider |
| Permission and Approval | PARTIAL | Separate default-deny Permission and exact action Approval; dispatch grants revalidate at claim | Policy records remain process-local; no production durable authority service |
| Dispatch TOCTOU | PARTIAL | Short-lived signed one-use grant is revalidated and atomically claimed immediately before handler | Claim means execution started; no atomic control over an external provider is claimed |
| Verification authority | IMPLEMENTED | Signed evidence binds subject/action/Task/Workflow/source/reference/digest/verifier/time/method/result/trust | Local HMAC authority; provider-specific external verifiers remain adapters |
| Tool execution | IMPLEMENTED | Authorization, grant claim, schema checks, configured timeout and UNKNOWN side-effect outcome | Blocking Python thread cannot be forcibly killed; no process sandbox |
| Model Router/runtime | PARTIAL | Replaceable capability router plus actual Task/Workflow usage accounting and conservative unknown usage | Local deterministic provider only; invocation fallback and provider cancellation remain limited |
| Resource controls | IMPLEMENTED | 50,000/Task and 300,000/Workflow defaults, accounting, hard stops, concurrency, leases, queues, rate windows | SQLite single-process qualification; no distributed quota coordination |
| Recovery | IMPLEMENTED | Finite deterministic decisions, durable operation/decision records, no blind uncertain-effect retry | Recovery decisions require caller composition |
| Event Bus | IMPLEMENTED | Durable local at-least-once delivery, ACK identity, finite retry, ordering key and dead letters | No exactly-once execution, global ordering, or external broker |
| Memory/State/Knowledge/Context | IMPLEMENTED | Separate scoped stores and transient permission-filtered Context | Lexical local behavior; no semantic/vector service |
| Observability/Activity | PARTIAL | Bounded redacted traces and durable Workflow history exposed through API/UI | No exporter, alerts, metrics backend, or distributed tracing |
| Integration Center | IMPLEMENTED | Definitions, multiple account connections, scopes, health, server-side OAuth/PKCE, callback, revoke | Real provider clients are not configured |
| Credential storage | IMPLEMENTED | AES-GCM vault and mode-0600 local key; refresh tokens never returned to frontend | No OS keychain/HSM integration or key rotation workflow |
| Google/Gmail/Drive/Calendar | NOT CONFIGURED | Definition and scope display only | Requires reviewed backend OAuth provider configuration and bounded tools |
| GitHub | NOT CONFIGURED | Definition and scope display only | Requires reviewed backend OAuth provider configuration and bounded tools |
| Instagram | NOT CONFIGURED | Definition and scope display only | Requires reviewed backend OAuth provider configuration and bounded tools |
| Development OAuth | SIMULATED | Safe state/PKCE/connect/disconnect test flow | Not an external account or production provider |
| External outreach | ADAPTER ONLY | Exact payload, Approval, dispatch grant, durable identity, uncertainty and verifier evidence | No credentialed email/CRM adapter |
| Freelancing delivery/QA/Handoff | IMPLEMENTED | Durable delivery operation, signed deliverable/QA evidence, atomic verified Handoff completion | No artifact repository or domain-specific external verifier |
| Browser/computer | UNAVAILABLE | Safe future boundary/status only | No control adapter is implemented |
| Voice | NOT CONFIGURED | Provider-neutral UI status; voice grants no authority | No STT/TTS/live-audio provider |
| Database operations | PARTIAL | WAL stores, stable IDs, integrity command, online backup, forward migration primitive | Historical stores are not all registered in one migration catalog; no encryption-at-rest for ordinary state |
| Production deployment | NOT IMPLEMENTED | None claimed | No TLS termination, service supervisor, HA, distributed locks, cloud or Kubernetes |

## 1.0 readiness decision

**NOT MET.** Version 0.11.0 is a usable local product. Production readiness still requires durable authenticated Permission/Approval composition, provider-specific OAuth and execution adapters, external verification, complete per-store migration registration, key rotation/backup operations, multi-process qualification, and deployment/security operations.

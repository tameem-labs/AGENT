# TESTING + DEFINITION OF DONE

Test layers:

- `tests/unit` — isolated contracts, stores, policies, and negative behavior;
- `tests/integration` — subsystem composition and the successful local Freelancing lifecycle;
- `tests/architecture` — executable ownership, dependency, authority, and forbidden-scope guards;
- `tests/security` — exact approval context and adversarial bounded-input behavior;
- `tests/restart` — durable idempotency, lease/crash recovery, stale writes, and terminal protection;
- `tests/e2e` — complete failure lifecycle and uncertainty/reconciliation evidence.

The established suites also cover permissions, approvals, model routing, tools, communication, Memory/State/Knowledge/Context, Recovery, Resources, and Observability. Computer control, UI, production providers, and distributed deployment are not implemented and are not represented as tested capabilities.

Negative tests cover unauthorized actions, expired/revoked authority, stale or changed approvals, failed/missing/conflicting verification, provider failure/timeout, duplicate callbacks/events/actions, partial delivery, uncertain side effects, hard resource stops, process restart, terminal-state protection, hostile input, bounded payloads, and secret leakage.

Final validation requires formatter, Ruff, strict mypy, complete and focused pytest, compile/import/CLI/package smoke, architecture/security/recovery/resource/restart/E2E suites, credential and forbidden-scope scans, `git diff --check`, and a complete diff/status review.

Done means implementation + tests + failure-path consideration + verification evidence + no known regression + honest docs/status + clean pushed worktree. “Build succeeded” or “tests exist” alone is not a production-readiness claim. See `PRODUCTION_READINESS.md` for the separate 1.0 assessment.

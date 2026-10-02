# FREELANCING DOMAIN

FREELANCE HEAD
├── LEAD-GEN HEAD
│   ├── Lead Research Agent
│   ├── Lead Qualification Agent
│   └── Lead Scoring Agent
├── OUTREACH HEAD
│   ├── Email Agent
│   └── CRM Agent
└── DELIVERY HEAD
    ├── Project Manager Agent
    └── QA Agent

Pipeline:
Find Lead → Research → Validate → Qualify → Score → CRM → Prepare Outreach → Human Approval → Send → Verify → Process Reply → Project → Delivery → QA → Handoff.

Events:
LEAD_FOUND
LEAD_QUALIFIED
OUTREACH_PREPARED / OUTREACH_APPROVAL_REQUIRED / OUTREACH_ACCEPTED / OUTREACH_DELIVERED / OUTREACH_VERIFIED / OUTREACH_FAILED / OUTREACH_UNCERTAIN
CLIENT_REPLY_RECEIVED
DELIVERY_TASK_RECORDED

Workflows:
Lead-to-Qualified
Outreach Sequence
Delivery Pipeline

## Implemented Round 1 boundary

Qualification remains the verified Phase 5 pipeline. Round 1 adds a bounded local operational continuation rather than a general Workflow engine.

Outreach preparations are immutable records containing qualified-lead provenance, recipient/channel, exact subject/body, personalization/source evidence, policy, permission capability, risk, and verification plan. Preparation does not send. `send_outreach` is always `STRICT_AUTHORIZATION` and executes only through Tool Registry → Tool Executor → standing Permission → existing action-specific Approval → registered channel handler. Approval displays and fingerprints bind the exact final message; expiry, cancellation, material change, or permission revocation blocks dispatch.

The external action ledger is durable and idempotent by `external_action_id`. `DISPATCHING` work interrupted by restart becomes `UNCERTAIN`; it is never blindly sent again. Phase 8 Recovery can request safe reconciliation or preserve uncertainty. Provider `ACCEPTED`, `DELIVERED`, and independently `VERIFIED` are distinct. The repository includes only an explicitly simulated deterministic adapter for tests; it never creates a real verified-delivery claim. Credentialed Email and CRM providers remain future adapters.

External reply intake validates and durably deduplicates normalized callback data before publishing `CLIENT_REPLY_RECEIVED`. Client text is untrusted data and cannot invoke tools or grant authority. Deterministic reply classification is advisory. Interested/project-like replies may create only a `PROJECT_PENDING` aggregate; activation is explicit compare-and-set state.

Delivery uses canonical Executive, Task, Agent Runtime, Resource reservations, Observability, and independent Verification. Project state tracks scope, dependencies, deadlines, deliverables, ownership, and verification requirements without becoming a general project-management product. QA records criteria, evidence, findings severity, and `PASS` / `FAIL` / `NEEDS_REVIEW` / `INCONCLUSIVE`. Handoff is `VERIFIED_COMPLETE` only when canonical delivery Tasks, deliverables, and QA evidence are verified and no issue remains. Unverified or failed work stays explicit.

No outreach, reply, score, model output, event, Memory record, or client instruction grants Permission or Approval. Any outbound response to a reply must re-enter the same prepare → permission → approval → Tool execution → verification path.

## Final Round 2 hardening

The exact approval boundary also matches requester, request, Task, and workflow identity at execution. Identical decision callbacks are idempotent; changed recipients, channels, arguments, contexts, or evidence fail closed. Uncertain outreach can persist a canonical Recovery operation/decision and correlated trace without resending.

Reply-event reconciliation, reply-to-project creation, delivery records, QA, and Handoff now reject materially conflicting duplicate identities. Identical QA/Handoff requests survive restart, and completed projects cannot revive. Verification accepts repeated identical evidence but rejects conflicting evidence. These are local SQLite/idempotency guarantees, not cross-store atomicity or exactly-once external execution.

Production email/CRM, webhook hosting, authenticated authority persistence, artifact storage, formal database migrations, deployment operations, and distributed coordination remain NOT IMPLEMENTED or ADAPTER ONLY. The deterministic outbound adapter remains SIMULATED.

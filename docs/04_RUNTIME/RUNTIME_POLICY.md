# RUNTIME POLICY

## Authority boundaries

Task and Workflow decide what may run. The Resource Manager decides only whether configured capacity is available. Recovery recommends a bounded safe disposition but does not execute, approve, authorize, verify, or mutate authoritative Task state except through the canonical Task adapter. Observability records operational facts and never becomes Task, State, or Memory authority. Permission, Approval, Execution, and Verification remain separate.

## Resources

The local Resource Manager controls task/workflow token budgets, task/agent/tool concurrency, provider/tool rate windows, queues, reservations, renewable expiring leases, accounting, and `INTERACTIVE`/`BACKGROUND` lanes. Defaults are 50,000 tokens per task and 300,000 per workflow; token and concurrency limits are configurable through non-secret TOML or environment settings. Rate policies are supplied through `ResourcePolicy` without source edits.

Usage is recorded as `EXACT`, `ESTIMATED`, or `UNKNOWN`; unknown provider usage is never represented as exact zero. Capacity shortages deterministically admit, queue, or block. Interactive work has preference while aged background work receives bounded fairness. Expired leases release capacity and startup reconciliation promotes queued work without double-counting.

A hard limit stops only affected work, reports usage and limit, preserves existing Task/State, records durable history, and never silently resets or creates a new budget. Resource exhaustion can be classified for Recovery, but Recovery cannot bypass the limit. Capacity is not permission or approval.

## Recovery

Failures use one structured taxonomy covering validation, authorization, approval, model, tool, network, timeout, persistence, resource exhaustion, process crash, unknown, external-side-effect uncertainty, verification, and dependency failures. Records retain available request/task/workflow/correlation, agent/instance, model/tool, approval/verification/recovery, message/event, and component identifiers.

Recovery decisions are deterministic and consider classification, retryability, finite attempt limits, bounded backoff, timeout, idempotency, resource availability, current state, and external side effects. Outcomes are retry, wait, fallback to an already registered compatible component, resume, escalate, stop, or mark uncertain. Model output cannot authorize a recovery decision. Escalation means safe continuation is unavailable; it grants no authority.

An uncertain external side effect is never blindly repeated or converted to success. Reconcile it only when a safe reconciliation mechanism exists; otherwise preserve uncertainty and escalate or stop. Verification failure does not automatically re-execute an external action. Durable startup reconciliation examines non-final operations, persists idempotent decisions, and never infers success or non-occurrence merely from process death.

## Observability

Operational traces are structured, durable, and queryable by bounded filters. Records carry applicable request/task/workflow/agent/instance/correlation, model/tool/approval/verification/recovery/message/event identifiers; timestamp, status, duration, attempt, failure classification, resource usage, and bounded metadata.

Metadata and structured logs are recursively redacted before persistence. Exception messages are not logged; only safe exception class information is emitted. Secrets, credentials, raw prompts, and unrestricted payloads do not belong in telemetry. Observer adapters are fail-open: telemetry failure cannot alter model, tool, runtime, verification, or Task behavior. Observability never mutates Task/State and is not automatically copied to Memory.

## Deployment limits

These services are synchronous and local SQLite implementations. They provide no distributed scheduler, worker fleet, broker, cloud coordination, monitoring SaaS, or autonomous self-healing. Product composition must explicitly invoke admission, accounting, recovery, and tracing boundaries.

## External actions and delivery

Outbound Freelancing actions are admitted only inside an already authorized Tool handler. Tool concurrency leases and configured provider rate windows are checked before adapter invocation and released afterward. A capacity/rate failure performs no external action. Delivery Tasks reserve task and agent capacity before canonical Executive execution; queued work remains unexecuted.

The durable external-action ledger claims a stable action identity before adapter invocation. A repeated terminal/uncertain identity returns its recorded result without calling the adapter. Process restart converts unresolved `DISPATCHING` state to `UNCERTAIN`; the Phase 8 Recovery policy permits only reconciliation when available, otherwise `MARK_UNCERTAIN`. It never turns timeout into success or authorizes a resend. Observability records operational IDs and statuses with bounded metadata, not exact reply bodies, credentials, or secret provider data.

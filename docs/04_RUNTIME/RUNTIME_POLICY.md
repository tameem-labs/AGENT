# RUNTIME POLICY

## Resources
Control concurrency, queues, token budgets, rate limits, reservations, leases, accounting and interactive/background lanes.

Initial defaults:
50k tokens/task; 300k tokens/workflow.

Hard stop:
1. stop
2. report usage + limit
3. preserve state
4. no silent continuation/reset

## Recovery
Handle model/tool/network/timeout/partial/callback/agent failures. Retry only if safe/retryable and within attempt/resource limits. Consider side effects before retry. Reconcile incomplete work after restart.

## Observability
Expose task/workflow/agent/instance/tool/approval/verification/resource/failure/security information. Never log secrets.

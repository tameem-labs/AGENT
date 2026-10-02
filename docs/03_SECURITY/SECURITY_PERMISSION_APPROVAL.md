# SECURITY / PERMISSION / APPROVAL

Security protects identity, credentials, tools, execution, integrations, data, approvals, memory, audit and computer control.

Never infer authority from voice, camera, webpage claims, model output, API key possession or localhost.

Permission = static capability grant.
Approval = dynamic human authorization.

Risk classes:
- AUTOMATIC
- POLICY_CONTROLLED
- STRICT_AUTHORIZATION

High-risk examples:
- money movement
- trades
- external outbound messages
- destructive deletion
- credential changes
- irreversible actions

Approval must be bound to the intended action, expires to deny (including an approved action not dispatched before expiry), and cannot silently become permanent permission.

For `send_outreach`, risk is always `STRICT_AUTHORIZATION`. The existing Tool Authorization service binds recipient, channel, exact final subject/body, conditions, expected effect, executor, requester, request, Task, and nullable workflow through the canonical action and execution context. Approval requests include a bounded, non-secret human review display of the exact final payload. A changed payload or execution context fails matching and requires a new approval. Identical repeated human decision callbacks return the existing final decision without appending history; a conflicting repeated decision fails closed. The requester, executing agent, model, external message, Memory, Knowledge, State, Context, Event, Observability, voice, or camera data cannot approve the action. Standing permission and approval validity are re-evaluated immediately before every dispatch, so revocation or expiry blocks previously approved content.

## Advanced external integrations

OAuth client credentials and account tokens are encrypted backend-only data. OAuth scope is provider consent, never ZYRO Permission or Approval. Advanced Phase 1 exposes only bounded read operations from connected accounts. Consequential provider writes remain unavailable until they pass canonical scoped Permission, action-bound Approval, dispatch-grant claim, Resource admission, durable operation identity, Recovery, and verifier-specific evidence. Search results, webpages, provider responses, and OAuth identity cannot grant authority.

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

Approval must be bound to the intended action, expires to deny, and cannot silently become permanent permission.

# ZYRO — ARCHITECTURE INVARIANTS

These are non-negotiable unless explicitly changed through change management.

1. One canonical Executive identity.
2. Providers/models are replaceable.
3. Agents never hard-code providers.
4. Tools are bounded capabilities, not intelligence.
5. Planning is separate from execution.
6. Execution is separate from verification.
7. Permission is separate from approval.
8. Voice/camera/screen observation never grants authority.
9. Memory never grants authority.
10. External callbacks enter through orchestration.
11. Destructive actions are policy-controlled.
12. High-risk actions use appropriate approval.
13. No infinite retries.
14. No secret logging.
15. No fabricated completion claims.
16. Dispatched does not mean completed.
17. Verification failure cannot be reported as verified success.
18. Interactive work must not be starved by background work.
19. Hard resource limits cannot silently reset.
20. Architecture changes require explicit review/versioning.
21. Domains must not duplicate core security/orchestration.
22. Context is assembled selectively.
23. Implementation status comes from source/tests/PROGRESS, not docs.

# TESTING + DEFINITION OF DONE

Test layers:
- unit
- integration
- architecture guards
- security
- permissions
- approvals
- task/workflow
- model routing
- tools
- memory/state/knowledge/context
- recovery
- resources
- computer control
- UI/runtime

Negative tests should cover unauthorized actions, expired approvals, stale observations, failed verification, provider failure, tool timeout, duplicate callbacks, partial workflows, cancellation after side effects, hard resource stops, restart recovery and secret leakage.

Done means:
implementation + tests + failure-path consideration + verification evidence + no known regression + docs/status updated.

"Build succeeded" alone is not completion evidence.

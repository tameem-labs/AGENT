# Advanced integrations and research

## Configuration and OAuth

Integration Center accepts OAuth **developer client** credentials for Google, GitHub, and Instagram. They are encrypted in the local AES-GCM vault and never returned. This is distinct from connecting a personal account: after configuration, select Connect, complete the provider's official consent flow, and let the backend consume one-use state/PKCE and exchange the code. Tokens remain backend-only. Multiple provider account identities are stored independently.

Google uses official Accounts, token, user-info, and revoke endpoints. GitHub uses official OAuth and API endpoints. Instagram uses official Instagram/Graph endpoints only; provider approval may be required. ZYRO does not scrape, automate passwords, or use private APIs.

OAuth scopes are provider permission, not ZYRO action authority. The current connected-account action API exposes read-only bounded operations and enforces recorded scopes. Consequential operations—Gmail draft/send/reply, Drive writes, Calendar writes, and GitHub mutation—are rejected until separately composed through canonical Permission, action-bound Approval, Resource admission, durable operation identity, Recovery, and provider-derived verification.

Implemented read actions:

- Gmail: list, search, read message, read thread.
- Drive: list, search, metadata.
- Calendar: list calendars/events, search/read events.
- GitHub: repositories, files, branches, commits, issues, pull requests, status.
- Instagram: profile and media where official permissions allow.

No live provider is claimed until its UI status is CONNECTED and its health/scopes support the requested operation.

## External research

Configure a Brave Search API key in Settings. Save & Test performs a real server-side query and persists the key only if it succeeds. A request beginning with `Research` is assigned to `zyro.research` through the normal Workflow and Executive Task path.

The Research Agent executes:

1. `research.web_search` through the official Brave Search API.
2. Up to three `research.read_source` calls with public HTTP(S)-only DNS/IP checks, restricted ports, redirect revalidation, content-type checks, byte and text limits, and timeouts.
3. Gemini synthesis using only supplied source evidence.
4. Structural Task verification and durable UI projection.

Each source preserves URL, SHA-256 content digest, and UTC retrieval time. Source text is untrusted data—not instructions, Permission, Approval, or external completion authority. Reports must state uncertainty because source coverage is bounded. Search and source Tools have explicit standing read-only permissions for the registered Research Agent, one-use dispatch grants, ToolExecutor validation/timeouts, Resource Manager concurrency leases, and model token accounting.

## External verification status

Provider responses and source digests are provenance evidence. They do not automatically prove semantic truth or successful external side effects. Existing signed `VerificationAuthority` evidence remains required for trusted domain completion. No caller boolean or arbitrary reference can create trusted evidence. Consequential provider tools must add verifier-specific provider evidence before they can be exposed.

# ZYRO — MASTER PRODUCT & SYSTEM SPECIFICATION

## 1. Product
ZYRO is a Personal Executive AI / Computer Agent / AI Organization. The user interacts with one continuous Executive while internally ZYRO coordinates domains, agents, models, tools, workflows, memory, state, knowledge, security, execution, verification and recovery.

Core principle:

> ONE EXECUTIVE. MANY DOMAINS. MANY AGENTS. MANY MODELS. MANY TOOLS. EXPLICIT AUTHORITY. EXPLICIT STATE. VERIFIED EXECUTION. HONEST REPORTING.

## 2. Organization
USER → ZYRO EXECUTIVE → DOMAIN HEAD → SUB-HEAD/TEAM LEAD (optional) → SPECIALIST/WORKER AGENTS → TOOLS → REAL WORLD.

Initial/future domains include Coding, Freelancing, Content, Research, Finance, Marketing, Sales, Operations, Personal Operations and Computer/Automation.

Hard delegation depth: 3 levels below a Domain Head unless explicitly changed.

## 3. Core architecture
INTERFACE
→ EXECUTIVE
→ IDENTITY
→ BRAIN / DECISION
→ ORCHESTRATION
   → TASK SYSTEM
   → WORKFLOW SYSTEM
   → AGENT RUNTIME
   → SCHEDULER / TRIGGERS
   → COMMUNICATION
   → MODEL ROUTER
→ RESOURCE MANAGER
→ MEMORY / STATE / KNOWLEDGE / REGISTRIES
→ TOOL REGISTRY
→ TOOLS
→ EXECUTION
→ PERMISSION + APPROVAL
→ SECURITY
→ REAL WORLD
→ OBSERVABILITY
→ RECOVERY

## 4. Mandatory distinctions
Agent ≠ Model
Agent ≠ Tool
Agent ≠ Workflow
Task ≠ Workflow
Memory ≠ Knowledge
Memory ≠ State
State ≠ Knowledge
Context ≠ Memory
Planning ≠ Execution
Execution ≠ Verification
Permission ≠ Approval
Direct Message ≠ Event
Identity ≠ Brain
Identity ≠ Memory
Domain ≠ Core
Interface ≠ Intelligence

## 5. Executive/Brain
The Brain reasons, interprets, plans, prioritizes and delegates. It is not a specific model. Models are replaceable engines behind the Model Router.

## 6. Capability planning
For a meaningful goal, ZYRO should determine:
CAPABILITY / AGENT / MODEL CLASS / TOOLS / RISK / PERMISSION / APPROVAL / VERIFICATION / FALLBACK.

Classify proposals as Required, Recommended, Optional, Future or Experimental. Major architecture changes require explicit approval/review.

## 7. Model strategy
Default development preference: Gemini where suitable and economical/free-tier-friendly. Use lightweight models for simple work, stronger reasoning/coding models for hard work, multimodal models for vision, and Live/native-audio models for voice where appropriate.

Agents must never hard-code a provider. The Model Router selects using task type, complexity, risk, latency, cost, context, modality, privacy, tool-calling, reliability and availability. Exact provider/model names and quotas remain configuration.

## 8. Computer agent
Long-term Windows capabilities:
- browser/navigation
- filesystem
- terminal
- application control
- screenshots
- screen understanding/OCR
- form filling
- builds/tests/log analysis
- repetitive automation
- pause/resume/cancel/approve/reject/retry where policy permits

Screen observation is not authority. Stale observation must not trigger consequential action.

## 9. Voice
VOICE INPUT → STT/LIVE AUDIO → ZYRO → REASONING → EXECUTION → RESPONSE → SPEECH.

Desired: natural conversation, interruption/barge-in, low latency, stable identity and fallback. Voice does not grant permission or approval.

## 10. Camera
Future: off by default, explicit control, visible indicator, visual understanding only, no biometric inference. Camera observation does not grant authority.

## 11. Memory / Knowledge / State / Context
Memory = historical records.
Knowledge = indexed/reference information.
State = current live status.
Context = transient task-specific assembly.

Context is selected by relevance, scope, permission, recency, priority and budget. Never dump all context.

Suggested retention:
- Task/Workflow/Agent: 7 days
- Project/Historical Outcome/Domain/System: 90 days
- User/Preference/Decision/Relationship: owner-controlled

Secrets/credentials never enter ordinary memory. Memory never grants permission.

## 12. Security
Security covers owner identity, credentials, tools, execution, integrations, data, approvals, memory, audit and computer control.

Do not infer authority from voice, camera, webpage claims, model output, API-key possession or localhost access.

Identity ≠ Permission ≠ Approval ≠ Execution Authority.

## 13. Permission / Approval
Permission is a static capability grant. Approval is a dynamic human gate.

Risk classes:
- AUTOMATIC
- POLICY_CONTROLLED
- STRICT_AUTHORIZATION

High-risk examples: money movement, trades, external messages, destructive deletion, credential changes and irreversible actions. Approval expires to deny; re-escalation may ask again but never silently execute.

## 14. Task lifecycle
PENDING → RUNNING → VERIFYING → VERIFIED → DONE.

Failure can lead to RETRY or FAILED. Timeout can lead to RETRY or FAILED. Any non-final state can be CANCELLED.

If no verification exists, report explicitly as unverified. Dispatched ≠ completed.

User-facing states may include NOT_STARTED, RUNNING, WAITING, WAITING_FOR_APPROVAL, VERIFYING, SUCCEEDED, VERIFIED, SUCCEEDED_BUT_UNVERIFIED, FAILED, RETRYING, CANCELLED, TIMED_OUT, PARTIALLY_COMPLETED and BLOCKED.

## 15. Communication
Direct messages carry:
message_id, request_id, task_id, workflow_id, correlation_id, sender, receiver, message_type, priority, payload, response_required, timestamp, version.

Events carry:
event_id, request_id, task_id, workflow_id, correlation_id, event_type, publisher, payload, delivery policy, timestamp, version.

External callbacks/webhooks enter through intake/event/orchestration.

## 16. Resources
Resource Manager controls concurrency, queues, token budgets, API rate limits, reservations, leases, accounting and interactive/background lanes.

Initial configurable defaults:
- 50,000 tokens/task
- 300,000 tokens/workflow

Hard stop: stop, report exact usage/limit, preserve state, do not silently reset.

## 17. Observability
Expose current task/workflow/agent, waiting reason, approval, verification, failure, resource usage and security issues.

Trace identifiers:
request_id, task_id, workflow_id, agent_id, instance_id, correlation_id.

Never log secrets.

## 18. Recovery
Handle model/tool/network/timeout/partial/callback/agent failures. Retry only when safe/retryable. No infinite retry.

After restart reconcile running/completed/failed/uncertain work.

## 19. First proving domain: Freelancing
FREELANCE HEAD
├── LEAD-GEN HEAD
│   ├── Lead Research
│   ├── Lead Qualification
│   └── Lead Scoring
├── OUTREACH HEAD
│   ├── Email
│   └── CRM
└── DELIVERY HEAD
    ├── Project Management
    └── QA

Pipeline:
Find Lead → Research → Validate → Qualify → Score → CRM → Prepare Outreach → Human Approval → Send → Verify → Process Reply → Project → Delivery → QA → Handoff.

Initial events: LEAD_FOUND, LEAD_QUALIFIED, CLIENT_REPLIED.
Initial workflows: Lead-to-Qualified, Outreach Sequence, Delivery Pipeline.

## 20. Content
Future:
Trend Discovery → Research → Fact Check → Script → Visual Planning → Voice → Edit → Platform Adaptation → Publish → Analytics.

Political/government content must use factual, neutral, sourced workflows.

## 21. Development philosophy
Build thin-first. Do not implement the whole future architecture before the first useful vertical slice works. Early in-process/SQLite equivalents are acceptable only where the roadmap explicitly permits them.

## 22. Final definition
ZYRO is a modular, model-agnostic, multimodal personal executive AI and computer agent in which one canonical Executive coordinates domains, agents, models, tools, memory, state, knowledge, security, permissions, execution, verification, recovery and communication while preserving user control and honest reporting.

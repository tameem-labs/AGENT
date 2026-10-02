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
CLIENT_REPLIED

Workflows:
Lead-to-Qualified
Outreach Sequence
Delivery Pipeline

Outbound messages must obey the approval policy. Exact final text, approval and send result should be auditable.

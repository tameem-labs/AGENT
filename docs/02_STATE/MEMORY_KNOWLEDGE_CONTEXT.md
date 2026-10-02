# MEMORY / KNOWLEDGE / STATE / CONTEXT

Memory = historical/system records.
Knowledge = indexed/reference information.
State = current live status.
Context = transient task-specific assembly.

Context Assembler can combine conversation, identity, memory, knowledge, state, task, workflow and domain data, filtered by relevance, scope, permissions, recency, priority and context budget.

Suggested retention:
- Task/Workflow/Agent: 7 days
- Project/Historical Outcome/Domain/System: 90 days
- User/Preference/Decision/Relationship: owner-controlled

Expired records are excluded from normal retrieval; expiry does not automatically imply physical deletion unless deletion policy says so.

Secrets/credentials never enter ordinary memory.
Every read/write carries requester identity and scope.
Memory never grants permission.

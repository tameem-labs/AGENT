# ZYRO — CORE CONTRACTS

## Agent
Definition metadata:
agent_id, name, version, role, domain, responsibilities, capabilities, permissions, risk_class, input_requirements, output_contract, context_requirements, communication_rules, model_requirements, verification_requirements, resource_limits.

Agent Definition = what the agent is.
Agent Instance = a runtime execution.

## Task
A bounded unit of work with task_id, request_id, goal, status, priority, owner, dependencies, assigned agent, budget, verification plan, timestamps and attempts. Task identifiers and plans are size-bounded; secret-shaped assignments and unbounded goals are rejected at construction.

Lifecycle:
PENDING → RUNNING → VERIFYING → VERIFIED → DONE.

## Workflow
Coordinates tasks toward a larger outcome. Must define tasks, dependencies, ordering/parallelism, failure policy, budget, verification and completion criteria.

Task ≠ Workflow.

## Tool
A bounded adapter with tool_id/version, input/output schemas, side effects, risk, permission, approval, timeout, retry, verification, secret and resource requirements.

Tools cannot grant permission or secretly select models.

## Model Router
Agents request capabilities, not providers. Router evaluates task type, complexity, risk, latency, cost, context, modality, privacy, tool calling, reliability and availability.

## External research evidence

The registered Research Agent executes canonical Tools rather than treating model output as external access. Search and source-read results retain provider/source, URL, digest, and retrieval time. These records establish provenance, not semantic truth. Structural Task verification does not silently convert source content into EXTERNAL trusted evidence; signed verifier-specific evidence remains required for external completion claims.

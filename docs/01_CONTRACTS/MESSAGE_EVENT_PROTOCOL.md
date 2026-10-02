# MESSAGE & EVENT PROTOCOL

## Direct Message

Locked canonical fields:

- `message_id`
- `request_id`
- `task_id`
- `workflow_id` (nullable)
- `correlation_id`
- `sender`
- `receiver`
- `message_type`
- `priority`
- `payload`
- `response_required`
- `timestamp`
- `version`

A Direct Message is point-to-point communication. Sender and receiver permissions are independently evaluated through the canonical Phase 4 permission boundary before handler invocation. Delivery is synchronous and bounded by an explicit finite policy. Identity-based deduplication uses `message_id`, never payload similarity. Response-required messages succeed only with an acknowledgement and response. Timeout enforcement is cooperative for synchronous handlers; no hidden worker thread is created.

## Event

Locked canonical fields:

- `event_id`
- `request_id`
- `task_id`
- `workflow_id` (nullable)
- `correlation_id`
- `event_type`
- `publisher`
- `payload`
- `delivery {durable, ack_required, ordering_key, retry_policy}`
- `timestamp`
- `version`

An Event is a published fact or signal. The local durable bus accepts only explicitly durable events after publish authorization and persists the canonical envelope before reporting safe acceptance. Subscriptions and handler bindings are not fields on the Event.

Delivery state is independently persisted per `(event_id, subscriber_id)`. Delivery is at-least-once with finite retries, persisted attempt identity, ACK correlation to event/subscriber/attempt, identity-based idempotency, subscriber-and-key ordering, durable dead letters, and restart recovery. Duplicate, stale, malformed, or unknown ACKs do not alter valid state. One subscriber's failure does not reverse another subscriber's ACK.

No exactly-once execution or global ordering is claimed. Ordering applies only to a configured ordering key for one subscriber. Handlers must be rebound after process restart; persisted subscriptions without a bound handler remain pending and observable. The Event Bus communicates facts and does not own canonical Task, lead, permission, approval, policy, or workflow state. Payloads are bounded untrusted data, reject nested secret-shaped fields/assignments, and never grant authority.

External webhooks/callbacks enter through intake and orchestration; they do not bypass these boundaries.

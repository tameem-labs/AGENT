# MESSAGE & EVENT PROTOCOL

## Direct message
message_id
request_id
task_id
workflow_id (nullable)
correlation_id
sender
receiver
message_type
priority
payload
response_required
timestamp
version

## Event
event_id
request_id
task_id
workflow_id
correlation_id
event_type
publisher
payload
delivery {durable, ack_required, ordering_key, retry_policy}
timestamp
version

Messages are directed communication.
Events are published facts/signals.
External webhooks/callbacks enter through intake and orchestration.

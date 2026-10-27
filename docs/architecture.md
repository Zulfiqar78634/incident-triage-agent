# Architecture decisions

## Runtime design

The agent uses an explicit, checkpointed state machine:

```text
created -> planned -> investigating -> investigated -> diagnosed
        -> awaiting_approval -> approved/rejected
        -> action_complete -> reported -> completed
```

The LLM is used for diagnosis and rationale. Deterministic runtime policy handles:

- Tool orchestration
- Retry and backoff
- Context compaction
- Approval routing
- State persistence
- Failure containment

This separation keeps the non-deterministic parts inspectable and prevents an LLM from silently bypassing safety controls.

## Why not a chat wrapper

A chat wrapper answers a message. This runtime owns a task lifecycle:

- It starts from an alert
- Maintains working state
- Calls tools across multiple steps
- Recovers from tool failures
- Pauses for human approval
- Resumes after a backend restart
- Produces an auditable outcome

## Tool execution

Tools are schema-driven and return a structured `ToolResult`:

```python
ToolResult(ok=True, data={...})
ToolResult(ok=False, error="...")
```

The runtime retries transient failures up to three times with increasing backoff. Repeated failures become explicit run failures rather than hidden model hallucinations.

## Persistence

SQLite is intentionally used for local reproducibility. The same design maps to Postgres in production:

- `runs.state` is the serialized checkpoint
- `events` is the append-only execution trace
- `approvals` is the human-gate ledger
- `long_term_memory` stores compact, reusable incident resolutions

## Production next steps

- Replace SQLite with Postgres
- Add Redis or a durable queue for async execution
- Add OpenTelemetry GenAI tracing
- Add role-based approval permissions
- Add real observability and deployment APIs with scoped credentials

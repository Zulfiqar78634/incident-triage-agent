# Context engineering

The agent treats context as a scarce, degrading resource.

## Context layers

| Layer | Included | Compaction strategy |
|---|---|---|
| System policy | Agent rules and safety constraints | Static and small |
| Incident state | Service, alert, severity | Always included |
| Evidence | Metrics, errors, runbook excerpt | Reduced to findings |
| Long-term memory | Similar prior resolutions | Limited to same incident type |
| Raw logs | Not sent directly | Converted to error counts and latest error |

## Retrieval precision

`search_runbook` scores runbooks against the alert and service, then returns only the strongest match with a bounded excerpt.

This is deliberate: retrieving more documents would increase token usage and can introduce contradictory or irrelevant guidance.

## Compaction

`compact_context()` builds a compact model-facing view from agent state. If it exceeds the configured budget, it truncates with an explicit marker rather than silently hiding the limitation.

## Memory promotion

At completion, the runtime stores:

- Incident type
- Compact diagnosis
- Resolution actions
- Confidence

It does not store the full transcript. This keeps future recall useful, cheap, and easier to govern.

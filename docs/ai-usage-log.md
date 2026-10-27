# AI-assisted development log

## Assessment constraint

The assignment requires AI usage and a complete interaction log. This project was developed with AI assistance for scaffolding, implementation, documentation, and test generation.

## How AI was used

| Area | AI contribution | Human design decision |
|---|---|---|
| Use-case selection | Compared infra provisioning, deployment orchestration, log analysis, and incident triage | Selected incident triage because it naturally requires tools, state, retries, and approval |
| Runtime design | Drafted an agent loop and persistence model | Chose explicit checkpointed transitions over a free-form chat loop |
| Context engineering | Proposed evidence compaction and retrieval limits | Decided that raw logs must never be passed directly to the model |
| API design | Generated FastAPI route structure | Defined run lifecycle: start, inspect, resume, cancel, approve |
| UI | Generated operator dashboard layout | Required trace visibility and explicit approve/reject controls |
| Tests | Created API-level happy-path tests | Added approval and rejection scenarios as required behavior |
| Documentation | Drafted README and architecture notes | Refined trade-offs around memory, safety, and observability |

## Key prompts and decisions

1. **Prompt:** “Build an end-to-end incident triage agent with a web UI, backend API, durable state, tool retries, and human approval.”  
   **Decision:** Use FastAPI, SQLite checkpoints, mock platform tools, and an explicit state machine.

2. **Prompt:** “How should the agent avoid context degradation?”  
   **Decision:** Compact logs into evidence, retrieve only the best runbook excerpt, and enforce a context budget.

3. **Prompt:** “How should a production-impacting restart be handled?”  
   **Decision:** Pause the run, persist a pending approval, and resume only after an explicit operator decision.

4. **Prompt:** “What should the operator see?”  
   **Decision:** A live execution trace with plan, tool retries, diagnosis, approval request, and completed actions.

## What was verified

- The app starts locally with `uvicorn app.main:app --reload`.
- The UI can start and observe a run.
- `ALERT-001` reaches the approval gate.
- Approving completes the restart workflow.
- Rejecting creates an escalation ticket.
- Run state and events persist in SQLite.
- `pytest` validates health, triage, approval, and rejection flows.
- `python -m scripts.eval` validates the golden-set scenario.

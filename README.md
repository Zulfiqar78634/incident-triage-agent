# Incident Triage Agent

An end-to-end AI agent that triages platform incidents: it plans an investigation, calls observability tools, compacts evidence, diagnoses the incident, and either takes a safe action or pauses for operator approval before restarting a production service.

It is a working FastAPI application with a browser-based operator UI, persistent agent state, resumable runs, tool retries, execution tracing, and an offline golden-set evaluation harness.

## Why incident triage

Incident triage exercises the hard parts of agent engineering:

- Multi-step investigation rather than one request/response LLM call
- Tool calls whose outputs compete for limited context
- A decision that can affect production, requiring human approval
- State that must survive process restarts and backend failures
- Operator visibility into what the agent did and why

## Architecture

```text
Browser UI
   |
FastAPI backend
   |
Agent runtime
   |-- Plan
   |-- Investigate (parallel tools + retry/backoff)
   |-- Diagnose (bounded context + long-term memory)
   |-- Approval gate
   |-- Execute / escalate
   |-- Report + write memory
   |
SQLite checkpoints, event trace, approvals, long-term memory
```

### Agent flow

1. Fetch the canonical alert.
2. Create an investigation plan.
3. Query metrics, logs, and the relevant runbook.
4. Compact tool output into evidence rather than passing raw logs to the model.
5. Diagnose the incident with a confidence score.
6. Route to a safe action, a ticket, or a human approval gate.
7. Resume from the persisted checkpoint after approval or rejection.
8. Report actions and store a compact resolution in long-term memory.

## Local development

### Requirements

- Python 3.11+

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

The application runs immediately in deterministic `MOCK_LLM` mode. To use a real model, add your key to `.env`:

```env
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
```

### Run tests

```bash
pytest
```

### Run evaluation

```bash
python -m scripts.eval
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/incidents` | Start an incident triage run |
| GET | `/api/runs/{run_id}` | Read state, events, diagnosis, and trace |
| POST | `/api/runs/{run_id}/resume` | Resume a persisted run |
| POST | `/api/runs/{run_id}/cancel` | Cancel a run |
| GET | `/api/approvals/{approval_id}` | Read a pending approval |
| POST | `/api/approvals/{approval_id}/decision` | Approve or reject remediation |
| GET | `/api/health` | Health check |

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/incidents \
  -H 'Content-Type: application/json' \
  -d '{"alert_id":"ALERT-001"}'
```

## Context engineering

The agent does not send an entire transcript or raw log file to the model.

- Each tool result is reduced to a bounded evidence finding.
- The runbook retriever returns the strongest match and a limited excerpt.
- `compact_context()` enforces a character budget.
- Long-term memory returns only prior resolutions for the same incident type.
- The model receives observations and evidence, not unbounded execution history.

A larger context window does not imply better reasoning. Irrelevant logs, stale tool output, and repeated history consume attention and increase cost; this agent treats context as a budgeted system resource.

## Memory design

| Layer | Purpose | Lifetime |
|---|---|---|
| Working state | Alert, plan, hypotheses, evidence, attempted tools, diagnosis | One run |
| Checkpoint | Exact resumable agent state | Until run completion/cancellation |
| Event trace | Every transition, tool retry, approval, and action | Audit/history |
| Long-term memory | Compact prior incident resolutions | Across sessions |

The runtime promotes a completed diagnosis and resolution into long-term memory. Future investigations of the same incident type can recall that compact history without replaying prior transcripts.

## Safety and human-in-the-loop

`restart_service` is treated as production-impacting.

The agent cannot execute it directly. It creates a pending approval, checkpoints the run, and waits. The operator can:

- Approve the restart
- Reject it, causing escalation through `create_ticket`
- Cancel the run

Tool calls have schemas, bounded arguments, retries with backoff, and structured error results. Unknown tools and missing arguments fail safely rather than crashing the runtime.

## Observability

Every meaningful transition emits an event:

- `run_started`
- `plan_created`
- `tool_retry`
- `investigation_complete`
- `diagnosis_created`
- `approval_requested`
- `approval_decided`
- `action_executed`
- `report_created`
- `run_completed`

The UI renders this trace so an operator can inspect the agent’s plan, evidence, decision, approval state, and actions—not only the final answer.

## What I would build next

- OpenTelemetry GenAI-compatible spans for LLM, tool, and agent steps
- Token, cost, and latency budgets with alerting
- Embedding-based runbook retrieval with relevance thresholds
- Prompt-injection screening and output validation
- Scenario-based offline evaluation with LLM-as-judge and human review
- Kubernetes/cloud tool integrations behind scoped, audited credentials

## AI usage

See [`docs/ai-usage-log.md`](docs/ai-usage-log.md) for the AI-assisted development transcript and decisions.

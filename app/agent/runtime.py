"""Durable incident-triage agent runtime.

Two layers. The lifecycle (plan -> investigate -> diagnose -> approval -> act ->
report) is an explicit, checkpointed state machine. Inside the investigate phase
the LLM drives a bounded plan/act/observe/replan loop: each turn it picks one
read-only tool from the real schemas, sees a compacted result, and either
continues or finishes. Every turn is one checkpointed step, so a run can resume
mid-investigation. Deterministic policy owns retries, budgets and the approval gate.
"""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from app.agent.context import summarize_tool_result
from app.agent.llm import LLM, get_llm
from app.agent.state import AgentState, Evidence, StepRecord
from app.agent.tools import execute_tool, selectable_tool_schemas
from app.db import append_event, create_approval, load_run, remember_resolution, save_run

MAX_TOOL_ATTEMPTS = 3
MAX_CONTEXT_CHARS = 6000
# Investigation budgets. The first one reached ends the loop (see state.stop_reason).
MAX_STEPS = 8
MAX_SECONDS = 60.0
MAX_TOKENS = 20000      # soft: checked after every LLM turn, so one call can cross it
MAX_REPEATS = 2          # identical successful calls tolerated before stopping
MAX_BAD_DECISIONS = 2    # invalid or unusable model turns tolerated
MAX_TRANSITIONS = 40     # hard cap on state-machine transitions per execution
# States where run_to_blocking_point stops: finished, or waiting on a human.
TERMINAL_STATUSES = {"awaiting_approval", "completed", "cancelled", "failed"}


class AgentRuntime:
    def __init__(self, llm: LLM | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._llm = llm
        self.sleep = sleep
        self.transitions: dict[str, Callable[[AgentState], AgentState]] = {
            "created": self._plan,
            "planned": self._investigate,
            "investigating": self._investigate,
            "investigated": self._diagnose,
            "diagnosed": self._route_action,
            "awaiting_approval": self._await_approval,
            "approved": self._execute_action,
            "rejected": self._handle_rejection,
            "acting": self._execute_action,
            "action_complete": self._report,
            "reported": self._finish,
        }

    @property
    def llm(self) -> LLM:
        return self._llm or get_llm()

    def start(self, alert_id: str) -> AgentState:
        run_id = str(uuid.uuid4())
        result = execute_tool("fetch_alert", {"alert_id": alert_id})
        if not result.ok:
            raise ValueError(result.error)

        alert = result.data
        state = AgentState(
            run_id=run_id,
            alert_id=alert_id,
            service=alert["service"],
            alert_message=alert["message"],
            severity=alert.get("severity", "unknown"),
            status="created",
        )
        append_event(run_id, "run_started", {"alert_id": alert_id, "service": state.service})
        self._persist(state)
        return state

    def resume(self, run_id: str) -> AgentState:
        loaded = load_run(run_id)
        if not loaded:
            raise ValueError(f"Unknown run: {run_id}")
        status, raw = loaded
        state = AgentState(**raw)
        state.status = status
        return self.step(state)

    def step(self, state: AgentState) -> AgentState:
        handler = self.transitions.get(state.status)
        if not handler:
            return state
        started = time.monotonic()
        state = handler(state)
        state.elapsed_s += time.monotonic() - started
        self._persist(state)
        return state

    def run_to_blocking_point(self, state: AgentState) -> AgentState:
        for _ in range(MAX_TRANSITIONS):
            if state.status in TERMINAL_STATUSES:
                return state
            state = self.step(state)
        state.status = "failed"
        append_event(state.run_id, "runtime_error", {"reason": "maximum transitions exceeded"})
        self._persist(state)
        return state

    def _persist(self, state: AgentState) -> None:
        save_run(state.run_id, state.status, state.model_dump())

    def _plan(self, state: AgentState) -> AgentState:
        state.plan = [
            f"Investigate {state.service} with read-only tools chosen by the model "
            f"(max {MAX_STEPS} steps, {int(MAX_SECONDS)}s, {MAX_TOKENS} tokens)",
            "Diagnose using compact evidence",
            "Choose a safe remediation or escalate",
        ]
        state.status = "planned"
        append_event(state.run_id, "plan_created", {"plan": state.plan})
        return state

    def _llm_call(self, state: AgentState, kind: str, fn: Callable):
        """Run one LLM turn with tracing; returns (value, meta) or (None, meta) on failure."""
        llm = self.llm
        meta = {"kind": kind, "mode": llm.mode, "model": llm.model, "provider": "google" if llm.mode == "gemini" else None}
        append_event(state.run_id, "llm_started", dict(meta))
        started = time.perf_counter()
        try:
            value, extra = fn(llm)
        except Exception as exc:
            meta.update(latency_ms=round((time.perf_counter() - started) * 1000), error_type=type(exc).__name__,
                        message="LLM turn failed or returned invalid output. No mock fallback was used.")
            append_event(state.run_id, "llm_failed", meta)
            return None, meta
        meta.update(extra)
        meta["latency_ms"] = round((time.perf_counter() - started) * 1000)
        state.tokens_used += meta.get("total_tokens") or 0
        append_event(state.run_id, "llm_completed", meta)
        return value, meta

    def _budget_stop(self, state: AgentState) -> str | None:
        if state.step_count >= MAX_STEPS:
            return "max_steps"
        if state.elapsed_s >= MAX_SECONDS:
            return "time_budget"
        if state.tokens_used >= MAX_TOKENS:
            return "token_budget"
        return None

    def _end_investigation(self, state: AgentState, reason: str) -> AgentState:
        state.stop_reason = reason
        state.status = "investigated"
        append_event(
            state.run_id,
            "investigation_complete",
            {"evidence_count": len(state.evidence), "steps": state.step_count, "stop_reason": reason,
             "tokens_used": state.tokens_used, "elapsed_s": round(state.elapsed_s, 2)},
        )
        return state

    def _investigate(self, state: AgentState) -> AgentState:
        """One turn of the loop. Checkpointed by step(), so it is resumable at turn granularity."""
        state.status = "investigating"
        stop = self._budget_stop(state)
        if stop:
            return self._end_investigation(state, stop)

        schemas = selectable_tool_schemas()
        decision, _ = self._llm_call(
            state, "next_step", lambda llm: llm.next_step(state, schemas, MAX_STEPS - state.step_count)
        )
        state.step_count += 1
        if decision is None:
            state.bad_decisions += 1
            return self._end_investigation(state, "llm_unavailable") if state.bad_decisions >= MAX_BAD_DECISIONS else state

        if state.tokens_used >= MAX_TOKENS:
            # The call that crossed the budget already happened; its tool call does not run.
            return self._end_investigation(state, "token_budget")

        append_event(
            state.run_id, "agent_decision",
            {"step": state.step_count, "action": decision.action, "tool": decision.tool, "args": decision.args, "reason": decision.reason},
        )
        if decision.action == "finish":
            return self._end_investigation(state, "model_finished")

        if decision.tool not in schemas:
            return self._reject_decision(state, decision, f"'{decision.tool}' is not an available investigation tool")
        signature = (decision.tool, json.dumps(decision.args, sort_keys=True))
        if any(s.ok and (s.tool, json.dumps(s.args, sort_keys=True)) == signature for s in state.steps):
            state.repeat_count += 1
            self._record_step(state, decision, False, "Duplicate call skipped: this exact call already succeeded", 0)
            return self._end_investigation(state, "repeated_calls") if state.repeat_count >= MAX_REPEATS else state

        result, attempts = self._call_tool(state, decision.tool, decision.args)
        if result.ok:
            summary = summarize_tool_result(decision.tool, result.data)
            if summary:
                state.evidence.append(Evidence(source=decision.tool, finding=summary[0], severity=summary[1]))
            self._record_step(state, decision, True, summary[0] if summary else "ok", attempts)
        else:
            self._record_step(state, decision, False, f"{result.error} (after {attempts} attempt(s))", attempts)
        return state

    def _reject_decision(self, state: AgentState, decision, why: str) -> AgentState:
        state.bad_decisions += 1
        self._record_step(state, decision, False, f"Rejected by runtime: {why}", 0)
        append_event(state.run_id, "decision_rejected", {"tool": decision.tool, "reason": why})
        return self._end_investigation(state, "bad_decisions") if state.bad_decisions >= MAX_BAD_DECISIONS else state

    def _record_step(self, state: AgentState, decision, ok: bool, observation: str, attempts: int) -> None:
        state.steps.append(StepRecord(n=state.step_count, tool=str(decision.tool), args=decision.args, ok=ok,
                                      observation=observation, reason=decision.reason, attempts=attempts))

    def _call_tool(self, state: AgentState, tool: str, args: dict):
        """Execute with retry/backoff for transient failures; permanent failures return immediately."""
        started = time.perf_counter()
        result = None
        for attempt in range(1, MAX_TOOL_ATTEMPTS + 1):
            result = execute_tool(tool, args)
            if result.ok or not result.retryable or attempt == MAX_TOOL_ATTEMPTS:
                break
            append_event(state.run_id, "tool_retry", {"tool": tool, "attempt": attempt, "error": result.error})
            self.sleep(min(2.0, 0.2 * 2 ** (attempt - 1)))
        append_event(
            state.run_id, "tool_call",
            {"tool": tool, "args": args, "ok": result.ok, "attempts": attempt, "error": result.error,
             "latency_ms": round((time.perf_counter() - started) * 1000)},
        )
        return result, attempt

    def _diagnose(self, state: AgentState) -> AgentState:
        if not state.evidence:
            diagnosis = {"diagnosis": f"No usable evidence gathered ({state.stop_reason}); operator investigation required.",
                         "confidence": 0.0, "proposed_action": "create_ticket"}
        else:
            memory = _load_memory_for_state(state)
            diagnosis, _ = self._llm_call(state, "diagnose", lambda llm: llm.diagnose(state, memory))
            if diagnosis is None:
                # Deliberately escalate instead of fabricating a successful model diagnosis.
                diagnosis = {"diagnosis": "Automated diagnosis failed; operator investigation required.",
                             "confidence": 0.0, "proposed_action": "create_ticket"}
        state.diagnosis = diagnosis["diagnosis"]
        state.confidence = float(diagnosis["confidence"])
        state.proposed_action = diagnosis.get("proposed_action")
        state.hypotheses = [diagnosis["diagnosis"]]
        state.status = "diagnosed"
        append_event(
            state.run_id,
            "diagnosis_created",
            {"diagnosis": state.diagnosis, "confidence": state.confidence, "action": state.proposed_action},
        )
        return state

    def _route_action(self, state: AgentState) -> AgentState:
        if state.proposed_action == "restart_service":
            approval_id = str(uuid.uuid4())
            state.approval_id = approval_id
            create_approval(
                {
                    "id": approval_id,
                    "run_id": state.run_id,
                    "action": "restart_service",
                    "rationale": state.diagnosis,
                    "risk": "production-impacting",
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            state.status = "awaiting_approval"
            append_event(state.run_id, "approval_requested", {"approval_id": approval_id, "action": "restart_service"})
            return state

        if state.proposed_action == "create_ticket":
            result = execute_tool("create_ticket", {"service": state.service, "summary": state.diagnosis})
            if result.ok:
                state.actions_taken.append(f"Created ticket {result.data['ticket_id']}")
                state.status = "action_complete"
            else:
                state.status = "failed"
            return state

        state.status = "action_complete"
        append_event(state.run_id, "no_action_required", {})
        return state

    def _await_approval(self, state: AgentState) -> AgentState:
        return state

    def _execute_action(self, state: AgentState) -> AgentState:
        result = execute_tool("restart_service", {"service": state.service})
        if result.ok:
            state.actions_taken.append(f"Restarted {state.service}")
            state.status = "action_complete"
        else:
            state.status = "failed"
        append_event(state.run_id, "action_executed", {"action": "restart_service", "ok": result.ok})
        return state

    def _handle_rejection(self, state: AgentState) -> AgentState:
        result = execute_tool("create_ticket", {"service": state.service, "summary": f"Escalation: {state.diagnosis}"})
        if result.ok:
            state.actions_taken.append(f"Created escalation ticket {result.data['ticket_id']}")
        state.status = "action_complete"
        append_event(state.run_id, "remediation_rejected", {"escalated": True})
        return state

    def _report(self, state: AgentState) -> AgentState:
        state.status = "reported"
        append_event(
            state.run_id,
            "report_created",
            {"diagnosis": state.diagnosis, "actions": state.actions_taken, "confidence": state.confidence},
        )
        return state

    def _finish(self, state: AgentState) -> AgentState:
        remember_resolution(
            incident_type=state.incident_type,
            summary=state.diagnosis,
            resolution="; ".join(state.actions_taken) or "No automated action taken",
            confidence=state.confidence,
        )
        state.status = "completed"
        append_event(state.run_id, "run_completed", {"memory_written": True})
        return state


def _load_memory_for_state(state: AgentState) -> list[dict]:
    from app.db import recall_similar_incidents

    return recall_similar_incidents(state.incident_type)


def compact_context(state: AgentState) -> str:
    """Bound the model-facing context instead of replaying the full transcript."""
    lines = [
        f"Service: {state.service}",
        f"Alert: {state.alert_message}",
        f"Severity: {state.severity}",
        "Evidence:",
    ]
    for item in state.evidence:
        lines.append(f"- [{item.source}] {item.finding}")
    text = "\n".join(lines)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n...[truncated by context budget]"
    return text


def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Model did not return JSON")
    return json.loads(match.group(0))

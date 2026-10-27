"""Durable incident-triage agent runtime.

The control flow is explicit and checkpointed. The LLM performs diagnosis and
communication; deterministic policy controls tool retries, context compaction,
and the human-approval gate. This keeps a non-deterministic system inspectable.
"""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from app.agent.llm import generate_diagnosis
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState, Evidence, ToolAttempt
from app.agent.tools import execute_tool
from app.db import append_event, create_approval, load_run, remember_resolution, save_run

MAX_TOOL_ATTEMPTS = 3
MAX_CONTEXT_CHARS = 6000
TERMINAL_STATUSES = {"awaiting_approval", "reported", "completed", "cancelled", "failed"}


class AgentRuntime:
    def __init__(self) -> None:
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
        state = handler(state)
        self._persist(state)
        return state

    def run_to_blocking_point(self, state: AgentState) -> AgentState:
        for _ in range(20):
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
            f"Fetch metrics for {state.service}",
            f"Inspect recent logs for {state.service}",
            "Retrieve the most relevant runbook",
            "Diagnose using compact evidence",
            "Choose a safe remediation or escalate",
        ]
        state.status = "planned"
        append_event(state.run_id, "plan_created", {"plan": state.plan})
        return state

    def _investigate(self, state: AgentState) -> AgentState:
        calls = [
            ("query_metrics", {"service": state.service}),
            ("query_logs", {"service": state.service, "minutes": 30}),
            ("search_runbook", {"query": f"{state.service} {state.alert_message}"}),
        ]
        for tool_name, args in calls:
            attempt = state.tool_attempts.get(tool_name, ToolAttempt(tool=tool_name, status="pending"))
            for attempt_number in range(1, MAX_TOOL_ATTEMPTS + 1):
                result = execute_tool(tool_name, args)
                attempt.attempts = attempt_number
                if result.ok:
                    attempt.status = "success"
                    state.tool_attempts[tool_name] = attempt
                    self._add_compact_evidence(state, tool_name, result.data)
                    break
                attempt.status = "retrying" if attempt_number < MAX_TOOL_ATTEMPTS else "failed"
                attempt.error = result.error
                state.tool_attempts[tool_name] = attempt
                append_event(
                    state.run_id,
                    "tool_retry",
                    {"tool": tool_name, "attempt": attempt_number, "error": result.error},
                )
                time.sleep(0.05 * attempt_number)

        failed = [name for name, item in state.tool_attempts.items() if item.status == "failed"]
        if failed:
            state.status = "failed"
            append_event(state.run_id, "investigation_failed", {"failed_tools": failed})
            return state

        state.status = "investigated"
        append_event(state.run_id, "investigation_complete", {"evidence_count": len(state.evidence)})
        return state

    def _add_compact_evidence(self, state: AgentState, source: str, data: dict) -> None:
        if source == "query_metrics":
            state.evidence.append(Evidence(source=source, finding=json.dumps(data), severity="warning"))
        elif source == "query_logs":
            errors = [e for e in data.get("entries", []) if e.get("level") == "ERROR"]
            finding = f"{len(errors)} ERROR entries; latest: {errors[-1]['message'] if errors else 'none'}"
            state.evidence.append(Evidence(source=source, finding=finding, severity="error" if errors else "info"))
        elif source == "search_runbook":
            matches = data.get("matches", [])
            if matches:
                state.evidence.append(
                    Evidence(source=source, finding=f"Runbook: {matches[0]['title']} — {matches[0]['excerpt'][:300]}")
                )

    def _diagnose(self, state: AgentState) -> AgentState:
        memory = _load_memory_for_state(state)
        diagnosis = generate_diagnosis(state, memory)
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

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runtime import AgentRuntime
from app.db import decide_approval, get_approval, list_events, load_run

router = APIRouter()
runtime = AgentRuntime()


class StartRunRequest(BaseModel):
    alert_id: str


class ApprovalDecision(BaseModel):
    decision: str


@router.post("/incidents")
def start_incident(request: StartRunRequest):
    try:
        state = runtime.start(request.alert_id)
        state = runtime.run_to_blocking_point(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": state.run_id, "status": state.status}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    loaded = load_run(run_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Run not found")
    status, state = loaded
    return {"run_id": run_id, "status": status, "state": state, "events": list_events(run_id)}


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str):
    try:
        state = runtime.resume(run_id)
        state = runtime.run_to_blocking_point(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": state.run_id, "status": state.status}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    loaded = load_run(run_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Run not found")
    status, state = loaded
    state["status"] = "cancelled"
    from app.db import save_run, append_event

    save_run(run_id, "cancelled", state)
    append_event(run_id, "run_cancelled", {})
    return {"run_id": run_id, "status": "cancelled"}


@router.get("/approvals/{approval_id}")
def read_approval(approval_id: str):
    approval = get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, request: ApprovalDecision):
    if request.decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    approval = decide_approval(approval_id, request.decision)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval["status"] == request.decision and approval.get("decided_at") is None:
        raise HTTPException(status_code=409, detail="Approval already decided")

    state_status, state = load_run(approval["run_id"])
    state["status"] = "approved" if request.decision == "approved" else "rejected"
    from app.db import save_run, append_event

    save_run(approval["run_id"], state["status"], state)
    append_event(approval["run_id"], "approval_decided", {"decision": request.decision})

    resumed = runtime.run_to_blocking_point(type(runtime.transitions.keys().__iter__().__next__(), )) if False else runtime.resume(approval["run_id"])
    resumed = runtime.run_to_blocking_point(resumed)
    return {"run_id": approval["run_id"], "status": resumed.status}


@router.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

"""Agent working state. This is the compact source of truth passed between steps."""

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source: str
    finding: str
    severity: str = "info"


class StepRecord(BaseModel):
    """One investigation step: what the model asked for and what came back."""

    n: int
    tool: str
    args: dict = Field(default_factory=dict)
    ok: bool
    observation: str
    reason: str = ""
    attempts: int = 1


class AgentState(BaseModel):
    run_id: str
    alert_id: str
    service: str
    alert_message: str
    severity: str = "unknown"
    incident_type: str = "unknown"
    plan: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    diagnosis: str = ""
    confidence: float = 0.0
    proposed_action: str | None = None
    approval_id: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    status: str = "created"
    # Budgets: persisted so they survive restarts and resumes.
    step_count: int = 0
    tokens_used: int = 0
    elapsed_s: float = 0.0
    repeat_count: int = 0
    bad_decisions: int = 0
    stop_reason: str | None = None

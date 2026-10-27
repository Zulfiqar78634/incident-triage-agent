"""Agent working state. This is the compact source of truth passed between steps."""

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source: str
    finding: str
    severity: str = "info"


class ToolAttempt(BaseModel):
    tool: str
    status: str
    attempts: int = 0
    error: str | None = None


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
    tool_attempts: dict[str, ToolAttempt] = Field(default_factory=dict)
    diagnosis: str = ""
    confidence: float = 0.0
    proposed_action: str | None = None
    approval_id: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    status: str = "created"
    context_tokens_used: int = 0
    retries: int = 0

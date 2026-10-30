"""Schema-validated tools backed by deterministic fixtures.

Every tool declares a Pydantic argument model. The same models produce the JSON
schemas shown to the LLM and validate the arguments it returns, so there is one
source of truth. In production these would call observability, ticketing and
deployment systems.
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
SERVICE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"


class ToolResult(BaseModel):
    ok: bool
    data: dict = Field(default_factory=dict)
    error: str | None = None
    retryable: bool = True  # False for validation errors and permanent "not found"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FetchAlertArgs(_Args):
    alert_id: str = Field(min_length=1, max_length=64)


class QueryLogsArgs(_Args):
    service: str = Field(pattern=SERVICE_PATTERN)
    minutes: int = Field(default=15, ge=1, le=120)


class QueryMetricsArgs(_Args):
    service: str = Field(pattern=SERVICE_PATTERN)


class SearchRunbookArgs(_Args):
    query: str = Field(min_length=1, max_length=200)


class CreateTicketArgs(_Args):
    service: str = Field(pattern=SERVICE_PATTERN)
    summary: str = Field(min_length=1, max_length=500)


class RestartServiceArgs(_Args):
    service: str = Field(pattern=SERVICE_PATTERN)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def fetch_alert(alert_id: str) -> ToolResult:
    alert = _load("alerts.json").get(alert_id)
    if not alert:
        return ToolResult(ok=False, error=f"Unknown alert_id: {alert_id}", retryable=False)
    return ToolResult(ok=True, data=alert)


def query_logs(service: str, minutes: int = 15) -> ToolResult:
    logs = _load("logs.json")
    return ToolResult(ok=True, data={"service": service, "window_minutes": minutes, "entries": logs.get(service, [])})


def query_metrics(service: str) -> ToolResult:
    metrics = _load("metrics.json")
    if service not in metrics:
        return ToolResult(ok=False, error=f"No metrics for service: {service}", retryable=False)
    return ToolResult(ok=True, data=metrics[service])


def search_runbook(query: str) -> ToolResult:
    terms = set(query.lower().split())
    scored = []
    for item in _load("runbooks.json"):
        text = f"{item['title']} {item['keywords']} {item['content']}".lower()
        score = len(terms & set(text.split())) / max(len(terms), 1)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return ToolResult(ok=True, data={"matches": []})
    # Precision over volume: only the strongest match and a bounded excerpt.
    best = scored[0][1]
    return ToolResult(
        ok=True,
        data={"matches": [{"title": best["title"], "excerpt": best["content"][:700], "relevance": round(scored[0][0], 3)}]},
    )


def create_ticket(service: str, summary: str) -> ToolResult:
    ticket_id = f"INC-{random.randint(10000, 99999)}"
    return ToolResult(ok=True, data={"ticket_id": ticket_id, "service": service, "summary": summary[:240]})


def restart_service(service: str) -> ToolResult:
    return ToolResult(ok=True, data={"service": service, "action": "restart", "result": "accepted"})


@dataclass(frozen=True)
class Tool:
    description: str
    args: type[BaseModel]
    fn: Callable[..., ToolResult]
    agent_selectable: bool  # may the LLM pick it during investigation?
    side_effect: bool


TOOLS: dict[str, Tool] = {
    "fetch_alert": Tool("Fetch the canonical alert record.", FetchAlertArgs, fetch_alert, False, False),
    "query_metrics": Tool("Current health metrics for a service: error rate, p99 latency, memory and CPU percent.", QueryMetricsArgs, query_metrics, True, False),
    "query_logs": Tool("Recent log entries for a service within a time window (minutes).", QueryLogsArgs, query_logs, True, False),
    "search_runbook": Tool("Retrieve the best-matching runbook excerpt for a free-text query.", SearchRunbookArgs, search_runbook, True, False),
    "create_ticket": Tool("Create an incident ticket. Chosen by the runtime, not by the investigation loop.", CreateTicketArgs, create_ticket, False, True),
    "restart_service": Tool("Restart a service. Requires operator approval, enforced by the runtime.", RestartServiceArgs, restart_service, False, True),
}


def _strip_titles(node):
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    return node


def _schema(tool: Tool) -> dict:
    return _strip_titles(tool.args.model_json_schema())


TOOL_SCHEMAS = {
    name: {"description": t.description, "side_effect": t.side_effect, "agent_selectable": t.agent_selectable, "parameters": _schema(t)}
    for name, t in TOOLS.items()
}


def selectable_tool_schemas() -> dict:
    """The only tools the LLM may choose during investigation (read-only)."""
    return {n: {"description": s["description"], "parameters": s["parameters"]} for n, s in TOOL_SCHEMAS.items() if s["agent_selectable"]}


def execute_tool(name: str, args: dict) -> ToolResult:
    tool = TOOLS.get(name)
    if not tool:
        return ToolResult(ok=False, error=f"Unknown tool: {name}", retryable=False)
    try:
        parsed = tool.args.model_validate(args)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return ToolResult(ok=False, error=f"Invalid arguments for {name}: {detail}", retryable=False)
    try:
        return tool.fn(**parsed.model_dump())
    except Exception as exc:  # tool boundary converts unexpected failures into recoverable errors
        return ToolResult(ok=False, error=str(exc))

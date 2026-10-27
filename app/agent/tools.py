"""Sandboxed, schema-validated tools backed by deterministic fixtures.

In production these would call observability, ticketing, and deployment systems.
For the assessment they are local, reproducible, and safe to run.
"""

import json
import random
from pathlib import Path
from pydantic import BaseModel, Field

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class ToolResult(BaseModel):
    ok: bool
    data: dict = Field(default_factory=dict)
    error: str | None = None


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def fetch_alert(alert_id: str) -> ToolResult:
    alerts = _load("alerts.json")
    alert = alerts.get(alert_id)
    if not alert:
        return ToolResult(ok=False, error=f"Unknown alert_id: {alert_id}")
    return ToolResult(ok=True, data=alert)


def query_logs(service: str, minutes: int = 15) -> ToolResult:
    if minutes < 1 or minutes > 120:
        return ToolResult(ok=False, error="minutes must be between 1 and 120")
    logs = _load("logs.json")
    return ToolResult(ok=True, data={"service": service, "window_minutes": minutes, "entries": logs.get(service, [])})


def query_metrics(service: str) -> ToolResult:
    metrics = _load("metrics.json")
    if service not in metrics:
        return ToolResult(ok=False, error=f"No metrics for service: {service}")
    return ToolResult(ok=True, data=metrics[service])


def search_runbook(query: str) -> ToolResult:
    runbooks = _load("runbooks.json")
    terms = set(query.lower().split())
    scored = []
    for item in runbooks:
        text = f"{item['title']} {item['keywords']} {item['content']}".lower()
        score = len(terms & set(text.split())) / max(len(terms), 1)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return ToolResult(ok=True, data={"matches": []})
    # Precision over volume: return only the strongest match and a bounded excerpt.
    best = scored[0][1]
    return ToolResult(
        ok=True,
        data={
            "matches": [
                {
                    "title": best["title"],
                    "excerpt": best["content"][:700],
                    "relevance": round(scored[0][0], 3),
                }
            ]
        },
    )


def create_ticket(service: str, summary: str) -> ToolResult:
    if not service or not summary:
        return ToolResult(ok=False, error="service and summary are required")
    ticket_id = f"INC-{random.randint(10000, 99999)}"
    return ToolResult(ok=True, data={"ticket_id": ticket_id, "service": service, "summary": summary[:240]})


def restart_service(service: str) -> ToolResult:
    # This is intentionally gated behind human approval by the runtime.
    return ToolResult(ok=True, data={"service": service, "action": "restart", "result": "accepted"})


TOOL_SCHEMAS = {
    "fetch_alert": {
        "description": "Fetch the canonical alert record.",
        "args": {"alert_id": "str"},
    },
    "query_logs": {
        "description": "Query recent service logs.",
        "args": {"service": "str", "minutes": "int=15"},
    },
    "query_metrics": {
        "description": "Query service health metrics.",
        "args": {"service": "str"},
    },
    "search_runbook": {
        "description": "Retrieve the most relevant runbook excerpt.",
        "args": {"query": "str"},
    },
    "create_ticket": {
        "description": "Create an incident ticket.",
        "args": {"service": "str", "summary": "str"},
    },
    "restart_service": {
        "description": "Restart a service. Requires operator approval.",
        "args": {"service": "str"},
    },
}


def execute_tool(name: str, args: dict) -> ToolResult:
    handlers = {
        "fetch_alert": lambda: fetch_alert(args["alert_id"]),
        "query_logs": lambda: query_logs(args["service"], int(args.get("minutes", 15))),
        "query_metrics": lambda: query_metrics(args["service"]),
        "search_runbook": lambda: search_runbook(args["query"]),
        "create_ticket": lambda: create_ticket(args["service"], args["summary"]),
        "restart_service": lambda: restart_service(args["service"]),
    }
    if name not in handlers:
        return ToolResult(ok=False, error=f"Unknown tool: {name}")
    try:
        return handlers[name]()
    except KeyError as exc:
        return ToolResult(ok=False, error=f"Missing argument: {exc}")
    except Exception as exc:  # tool boundary converts unexpected failures into recoverable errors
        return ToolResult(ok=False, error=str(exc))

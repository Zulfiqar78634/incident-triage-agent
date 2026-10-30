import json
import re

MAX_CONTEXT_CHARS = 6000
MAX_OBSERVATION_CHARS = 300


def compact_context(state):
    lines = [f'Service: {state.service}', f'Alert: {state.alert_message}',
             f'Severity: {state.severity}', 'Evidence:']
    lines.extend(f'- [{item.source}] {item.finding}' for item in state.evidence)
    text = '\n'.join(lines)
    marker = '\n...[truncated by context budget]'
    return text if len(text) <= MAX_CONTEXT_CHARS else text[:MAX_CONTEXT_CHARS-len(marker)] + marker

def extract_json(text):
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError('Model did not return JSON')
    return json.loads(match.group(0))


def summarize_tool_result(tool: str, data: dict):
    """Reduce a raw tool result to (finding, severity); raw output never reaches the model."""
    if tool == "query_metrics":
        return json.dumps(data), "warning"
    if tool == "query_logs":
        entries = data.get("entries", [])
        if not entries:
            return "No log entries in the requested window", "info"
        errors = [e for e in entries if e.get("level") == "ERROR"]
        latest = errors[-1]["message"] if errors else "none"
        return f"{len(errors)} ERROR entries of {len(entries)}; latest: {latest}", "error" if errors else "info"
    if tool == "search_runbook":
        matches = data.get("matches", [])
        if not matches:
            return "No runbook matched the query", "info"
        return f"Runbook: {matches[0]['title']} - {matches[0]['excerpt'][:300]}", "info"
    return None


def render_steps(state) -> str:
    """Bounded history of earlier tool calls for the planner prompt."""
    if not state.steps:
        return "(none yet)"
    return "\n".join(
        f"{s.n}. {s.tool}({json.dumps(s.args, sort_keys=True)}) -> "
        f"{'ok' if s.ok else 'FAILED'}: {s.observation[:MAX_OBSERVATION_CHARS]}"
        for s in state.steps
    )

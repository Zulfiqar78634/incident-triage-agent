import json
import re

MAX_CONTEXT_CHARS = 6000

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

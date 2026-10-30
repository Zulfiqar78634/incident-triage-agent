"""LLM adapters. The runtime depends on the LLM protocol, never on a vendor SDK.

Two roles: `next_step` (pick the next read-only tool or finish) and `diagnose`.
`MockLLM` is the explicit offline path and is always labelled mode="mock".
"""
import json
import os
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.agent.context import compact_context, extract_json, render_steps
from app.agent.prompts import DIAGNOSIS_PROMPT, INVESTIGATE_PROMPT, INVESTIGATE_SYSTEM, SYSTEM_PROMPT


class Diagnosis(BaseModel):
    diagnosis: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    proposed_action: Literal['restart_service', 'create_ticket', 'none']
    rationale: str


class Decision(BaseModel):
    action: Literal['call_tool', 'finish']
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    reason: str = Field(default='', max_length=500)


class LLM(Protocol):
    mode: str
    model: str | None

    def next_step(self, state, tool_schemas: dict, steps_left: int) -> tuple[Decision, dict]: ...
    def diagnose(self, state, memory: list[dict]) -> tuple[dict, dict]: ...


class GeminiLLM:
    mode = 'gemini'

    def __init__(self, key: str, model: str) -> None:
        self.key, self.model = key, model

    def _generate(self, prompt: str, system: str) -> tuple[str, int | None]:
        from google import genai
        with genai.Client(api_key=self.key, http_options={'timeout': 30000}) as client:
            response = client.models.generate_content(
                model=self.model, contents=prompt,
                config={'system_instruction': system, 'temperature': 0.1,
                        'response_mime_type': 'application/json'})
        usage = getattr(response, 'usage_metadata', None)
        return response.text or '{}', getattr(usage, 'total_token_count', None)

    def next_step(self, state, tool_schemas, steps_left):
        prompt = INVESTIGATE_PROMPT.format(
            service=state.service, alert=state.alert_message, severity=state.severity,
            tools=json.dumps(tool_schemas, indent=2), steps=render_steps(state),
            steps_left=steps_left)
        text, tokens = self._generate(prompt, INVESTIGATE_SYSTEM)
        return Decision.model_validate(extract_json(text)), {'total_tokens': tokens}

    def diagnose(self, state, memory):
        prompt = DIAGNOSIS_PROMPT.format(
            service=state.service, alert=state.alert_message, severity=state.severity,
            evidence=compact_context(state), memory=json.dumps(memory, indent=2) if memory else 'None')
        text, tokens = self._generate(prompt, SYSTEM_PROMPT)
        return Diagnosis.model_validate(extract_json(text)).model_dump(), {'total_tokens': tokens}


class MockLLM:
    """Deterministic offline policy. Not a model: it exists so the app runs without a key."""
    mode = 'mock'
    model = None
    ORDER = ('query_metrics', 'query_logs', 'search_runbook')

    def next_step(self, state, tool_schemas, steps_left):
        tried = {s.tool for s in state.steps}
        for tool in self.ORDER:
            if tool in tried or tool not in tool_schemas:
                continue
            args = {'query_metrics': {'service': state.service},
                    'query_logs': {'service': state.service, 'minutes': 30},
                    'search_runbook': {'query': f'{state.service} {state.alert_message}'}}[tool]
            return Decision(action='call_tool', tool=tool, args=args, reason=f'Offline policy: gather {tool} evidence'), {}
        return Decision(action='finish', reason='Offline policy: all read-only sources consulted'), {}

    def diagnose(self, state, memory):
        message = state.alert_message.lower()
        if any(word in message for word in ('memory', 'oom', '5xx')):
            result = {'diagnosis': f'{state.service} is unhealthy; repeated errors indicate a service-level failure requiring remediation.',
                      'confidence': 0.86, 'proposed_action': 'restart_service',
                      'rationale': 'Offline rule-based demonstration; restart requires human approval.'}
        else:
            result = {'diagnosis': f'{state.service} alert requires investigation but no immediate production remediation is indicated.',
                      'confidence': 0.62, 'proposed_action': 'create_ticket',
                      'rationale': 'Offline rule-based demonstration; escalate for investigation.'}
        return Diagnosis.model_validate(result).model_dump(), {}


def get_llm() -> LLM:
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if key:
        return GeminiLLM(key, os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'))
    return MockLLM()

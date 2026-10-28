"""Server-side Gemini adapter. Offline rules are explicitly labelled mock."""
import json
import os
import time
from pydantic import BaseModel, Field
from typing import Literal
from app.agent.context import compact_context, extract_json
from app.agent.prompts import DIAGNOSIS_PROMPT, SYSTEM_PROMPT
from app.db import append_event

class Diagnosis(BaseModel):
    diagnosis: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    proposed_action: Literal['restart_service', 'create_ticket', 'none']
    rationale: str

def generate_diagnosis(state, memory: list[dict]) -> dict:
    key = os.getenv('GEMINI_API_KEY', '').strip()
    mode = 'gemini' if key else 'mock'
    model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash') if key else None
    metadata = {'mode': mode, 'provider': 'google' if key else None,
                'model': model, 'llm_called': False}
    started = time.perf_counter()
    append_event(state.run_id, 'llm_started', metadata)
    try:
        if not key:
            result = _mock_diagnosis(state)
        else:
            from google import genai
            prompt = DIAGNOSIS_PROMPT.format(
                service=state.service, alert=state.alert_message,
                severity=state.severity, evidence=compact_context(state),
                memory=json.dumps(memory, indent=2) if memory else 'None')
            with genai.Client(api_key=key, http_options={'timeout': 30000}) as client:
                metadata['llm_called'] = True
                response = client.models.generate_content(
                    model=model, contents=prompt,
                    config={'system_instruction': SYSTEM_PROMPT, 'temperature': 0.1,
                            'response_mime_type': 'application/json'})
                result = extract_json(response.text or '{}')
                usage = getattr(response, 'usage_metadata', None)
                metadata['total_tokens'] = getattr(usage, 'total_token_count', None)
        validated = Diagnosis.model_validate(result).model_dump()
        metadata['latency_ms'] = round((time.perf_counter() - started) * 1000)
        append_event(state.run_id, 'llm_completed', metadata)
        return validated
    except Exception as exc:
        metadata['latency_ms'] = round((time.perf_counter() - started) * 1000)
        metadata['error_type'] = type(exc).__name__
        metadata['message'] = 'Diagnosis unavailable. Check key permissions, quota, model availability, and connectivity. No mock fallback was used.'
        append_event(state.run_id, 'llm_failed', metadata)
        # Deliberately escalate instead of fabricating a successful model diagnosis.
        return {'diagnosis': 'Automated diagnosis failed; operator investigation required.',
                'confidence': 0.0, 'proposed_action': 'create_ticket',
                'rationale': 'Model execution or response validation failed.'}

def _mock_diagnosis(state) -> dict:
    message = state.alert_message.lower()
    if any(word in message for word in ('memory', 'oom', '5xx')):
        return {'diagnosis': f'{state.service} is unhealthy; repeated errors indicate a service-level failure requiring remediation.',
                'confidence': 0.86, 'proposed_action': 'restart_service',
                'rationale': 'Offline rule-based demonstration; restart requires human approval.'}
    return {'diagnosis': f'{state.service} alert requires investigation but no immediate production remediation is indicated.',
            'confidence': 0.62, 'proposed_action': 'create_ticket',
            'rationale': 'Offline rule-based demonstration; escalate for investigation.'}

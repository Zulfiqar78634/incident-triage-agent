"""LLM adapter using Google Gemini, with deterministic offline mode.

The service works without an API key so reviewers can run it immediately.
Setting GEMINI_API_KEY switches diagnosis to a real Gemini model.
"""

import json
import os

from app.agent.prompts import DIAGNOSIS_PROMPT, SYSTEM_PROMPT
from app.agent.runtime import compact_context, extract_json


def generate_diagnosis(state, memory: list[dict]) -> dict:
    if not os.getenv("GEMINI_API_KEY"):
        return _mock_diagnosis(state)

    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = DIAGNOSIS_PROMPT.format(
        service=state.service,
        alert=state.alert_message,
        severity=state.severity,
        evidence=compact_context(state),
        memory=json.dumps(memory, indent=2) if memory else "None",
    )

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
        config={
            "system_instruction": SYSTEM_PROMPT,
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    )
    return extract_json(response.text or "{}")


def _mock_diagnosis(state) -> dict:
    message = state.alert_message.lower()
    if "memory" in message or "oom" in message or "5xx" in message:
        return {
            "diagnosis": f"{state.service} is unhealthy; repeated errors indicate a service-level failure requiring remediation.",
            "confidence": 0.86,
            "proposed_action": "restart_service",
            "rationale": "Metrics and logs indicate an active service failure; restart is the documented first remediation.",
        }
    return {
        "diagnosis": f"{state.service} alert requires investigation but no immediate production remediation is indicated.",
        "confidence": 0.62,
        "proposed_action": "create_ticket",
        "rationale": "Evidence is incomplete or the failure is not immediately actionable.",
    }

"""LLM adapter with a deterministic offline mode.

The service works without an API key so reviewers can run it immediately.
Setting OPENAI_API_KEY switches diagnosis to a real model.
"""

import json
import os
from app.agent.prompts import DIAGNOSIS_PROMPT, SYSTEM_PROMPT
from app.agent.runtime import compact_context, extract_json


def generate_diagnosis(state, memory: list[dict]) -> dict:
    if not os.getenv("OPENAI_API_KEY"):
        return _mock_diagnosis(state)

    from openai import OpenAI

    client = OpenAI()
    prompt = DIAGNOSIS_PROMPT.format(
        service=state.service,
        alert=state.alert_message,
        severity=state.severity,
        evidence=compact_context(state),
        memory=json.dumps(memory, indent=2) if memory else "None",
    )
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return extract_json(response.choices[0].message.content or "{}")


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

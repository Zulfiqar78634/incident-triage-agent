from types import SimpleNamespace
from app.agent.context import compact_context, extract_json
import pytest

def test_context_is_bounded():
    state = SimpleNamespace(service='demo', alert_message='x' * 10000, severity='warning', evidence=[])
    assert len(compact_context(state)) <= 6000

def test_extract_json_fenced():
    assert extract_json('```json\n{"ok": true}\n```') == {'ok': True}

def test_extract_json_invalid():
    with pytest.raises(ValueError):
        extract_json('not json')

def test_imports_without_cycle():
    from app.agent.llm import generate_diagnosis
    from app.agent.runtime import AgentRuntime
    assert callable(generate_diagnosis)
    assert AgentRuntime is not None

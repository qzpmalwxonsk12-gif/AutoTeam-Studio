from pathlib import Path
import pytest

from autoteam_studio.contracts import AgentEvent, Correction, RunSpec, RunSnapshot


def test_runspec_requires_a_prompt():
    with pytest.raises(ValueError):
        RunSpec(project_root=Path('.'), prompt='   ')


def test_runspec_bounds():
    with pytest.raises(ValueError):
        RunSpec(project_root=Path('.'), prompt='build app', review_rounds=0)
    with pytest.raises(ValueError):
        RunSpec(project_root=Path('.'), prompt='build app', max_call_seconds=3)


def test_shared_types_are_constructible():
    event = AgentEvent(run_id='run1', agent='a', timestamp='now', kind='message', message='Starting')
    snapshot = RunSnapshot(run_id='run1', status='running', reported_tokens_a=123)
    correction = Correction(id='c1', text='Use SQLite')
    assert event.agent == 'a'
    assert snapshot.reported_tokens_a == 123
    assert correction.delivered_b is False

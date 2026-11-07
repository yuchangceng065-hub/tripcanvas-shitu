"""Runtime budget guards: context-window cap, wall-clock TTL, and their wiring.

These are ordinary passing regressions (not xfails). They lock in that an
over-budget or stale run stops cleanly as `incomplete` with a clear reason,
never loops, never calls the model after the guard trips, and that a stale run
is refused on resume.
"""
import time
from datetime import date
from types import SimpleNamespace

import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    with TestClient(app) as c:
        yield c


def _make_data():
    from backend.planning import AlternativesBody, Requirements
    return AlternativesBody(
        title='预算护栏测试',
        requirements=Requirements(startDate=date(2026, 1, 1), days=1, mode='walk', people=2),
        candidateIds=['quyuan', 'museum'], optionalIds=[], useModel=False,
    )


def _fake_router():
    # The guard tests never invoke a tool, so the router is unused here.
    return SimpleNamespace()


def test_context_budget_stops_run_and_records_event(client, monkeypatch):
    from backend.agent import AgentSteps
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    monkeypatch.setenv('PLAN_CONTEXT_CHARS_BUDGET', '100')
    recorder = RunRecorder('owner-' + 'bgt', 'trip', 1, 'agent')
    steps = AgentSteps(_make_data(), _fake_router(), recorder)
    # Push the accumulated history past the (tiny) budget.
    steps.messages.append({'role': 'user', 'content': 'x' * 200})
    assert steps.over_context_budget() is True
    result = steps.terminate()
    assert result['status'] == 'incomplete'
    assert result['guard'] == 'context'
    phases = [e.phase for e in events_for(recorder.id)]
    assert 'context_exhausted' in phases, phases
    with SessionLocal() as session:
        row = session.get(Run, recorder.id)
        stored = __import__('json').loads(row.result)
    assert stored['status'] == 'incomplete'
    assert stored['guard'] == 'context'
    assert stored['contextChars'] > 100


def test_ttl_stops_expired_run_and_records_event(client, monkeypatch):
    from backend.agent import AgentSteps
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    monkeypatch.setenv('PLAN_RUN_TTL_SECONDS', '1')
    recorder = RunRecorder('owner-' + 'ttl', 'trip', 1, 'agent')
    steps = AgentSteps(_make_data(), _fake_router(), recorder)
    steps.recorder.created = time.time() - 9999  # simulate an abandoned run
    assert steps.run_expired() is True
    result = steps.terminate()
    assert result['guard'] == 'ttl'
    phases = [e.phase for e in events_for(recorder.id)]
    assert 'run_expired' in phases, phases
    with SessionLocal() as session:
        assert __import__('json').loads(session.get(Run, recorder.id).result)['guard'] == 'ttl'


def test_fresh_run_within_budgets_is_not_stopped(client, monkeypatch):
    from backend.agent import AgentSteps
    from backend.runlog import RunRecorder
    recorder = RunRecorder('owner-' + 'ok', 'trip', 1, 'agent')
    steps = AgentSteps(_make_data(), _fake_router(), recorder)
    assert steps.guard_violation() is None
    assert steps.context_chars_used() <= steps.context_budget
    assert steps.run_expired() is False


def test_ttl_takes_precedence_over_context(client, monkeypatch):
    from backend.agent import AgentSteps
    from backend.runlog import RunRecorder
    monkeypatch.setenv('PLAN_CONTEXT_CHARS_BUDGET', '100')
    monkeypatch.setenv('PLAN_RUN_TTL_SECONDS', '1')
    recorder = RunRecorder('owner-' + 'prec', 'trip', 1, 'agent')
    steps = AgentSteps(_make_data(), _fake_router(), recorder)
    steps.messages.append({'role': 'user', 'content': 'x' * 200})
    steps.recorder.created = time.time() - 9999
    # TTL is checked before context, so the reported reason is 'ttl'.
    assert steps.guard_violation() == 'ttl'
    assert steps.terminate()['guard'] == 'ttl'


def test_restart_refuses_a_run_past_ttl(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    monkeypatch.setenv('PLAN_RUN_TTL_SECONDS', '1')
    rec = RunRecorder('owner-' + 'resume', 'trip', 1, 'conversation')
    assert rec.finish('needs_input', {'question': '哪天出发？'})
    # Age the persisted run well past the TTL window.
    with SessionLocal.begin() as session:
        session.get(Run, rec.id).created = time.time() - 9999
    restored = RunRecorder.restore(rec.id)
    assert restored.restart() is False  # refused by TTL, not by sealing
    assert 'resumed' not in [e.phase for e in events_for(rec.id)]


def test_run_agent_stops_on_context_guard_without_model_call(client, monkeypatch):
    import asyncio
    from backend.agent import AgentSteps, run_agent
    from backend.runlog import RunRecorder, events_for
    monkeypatch.setenv('PLAN_CONTEXT_CHARS_BUDGET', '1')  # any history trips it
    called = {'decide': False}

    async def spy_decide(self):
        called['decide'] = True
        return None

    monkeypatch.setattr(AgentSteps, 'decide', spy_decide)
    recorder = RunRecorder('owner-' + 'loop', 'trip', 1, 'agent')
    result = asyncio.run(run_agent(_make_data(), _fake_router(), recorder))
    assert result['status'] == 'incomplete'
    assert result['guard'] == 'context'
    assert called['decide'] is False, 'guard must stop the loop before the model is invoked'
    assert 'context_exhausted' in [e.phase for e in events_for(recorder.id)]

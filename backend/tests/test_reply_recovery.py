"""Regression: a refused `RunRecorder.restart()` must not leave a consumed reply.

`claim_reply` commits the new session version before the planner can ask the run
to open a new activation. If that restart fails (sealed terminal, newer
generation, or any future refusal) the reply must be released again, no worker
may be scheduled, and the conversation must stay readable/retryable.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend.app import app, attempts
from backend.db import PlanningSession, Run, SessionLocal, User
from backend.conversation import create_session, load_session
from backend.runlog import RunRecorder, events_for


@pytest.fixture
def client(monkeypatch):
    attempts.clear()
    from backend.routers import planner
    # reply() calls this before touching the run; conftest forces rules mode.
    monkeypatch.setattr(planner, 'require_model', lambda: None)
    scheduled = []

    def fake_schedule(services, recorder, version, state):
        scheduled.append((recorder.id, version, state))

    monkeypatch.setattr(planner, 'schedule', fake_schedule)
    with TestClient(app) as c:
        yield c, scheduled


def _conversation(client):
    name = 'reply_' + uuid.uuid4().hex[:8]
    assert client.post('/api/v1/auth/register',
                       json={'username': name, 'password': 'test-password-123'}).status_code == 200
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.username == name)).id
    recorder = RunRecorder(owner, '', 0, 'conversation')
    state = {'engine': 'langgraph', 'phase': 'intake', 'title': '杭州两日',
             'turns': [{'role': 'user', 'text': '想去西湖'}], 'interpret': True,
             'facts': None, 'mentions': [], 'choices': {}, 'replies': 0,
             'preferences': {}}
    create_session(recorder, owner, state)
    with SessionLocal.begin() as session:
        session.execute(update(PlanningSession).where(PlanningSession.id == recorder.id)
                        .values(status='needs_input'))
    return recorder, owner, state


def _body(version=1):
    return {'version': version, 'requestId': uuid.uuid4().hex,
            'text': '两个人，步行', 'choices': {}, 'confirm': False, 'retry': False}


def test_reply_opens_one_activation_and_keeps_session_consistent(client):
    c, scheduled = client
    recorder, _, _ = _conversation(c)
    recorder.finish('needs_input', {'question': '哪天出发？'})
    response = c.post('/api/v1/planner/conversations/' + recorder.id + '/reply', json=_body())
    assert response.status_code == 200, response.text
    assert response.json() == {'runId': recorder.id, 'status': 'running',
                               'version': 2, 'idempotent': False}
    row = load_session(recorder.id)
    assert row['status'] == 'running' and row['version'] == 2
    assert row['state']['replies'] == 1 and row['state']['lastRequestId']
    with SessionLocal() as session:
        run = session.get(Run, recorder.id)
        assert run.generation == 1
        assert json.loads(run.result)['status'] == 'running'
    assert 'resumed' in [e.phase for e in events_for(recorder.id)]
    assert len(scheduled) == 1 and scheduled[0][1] == 2


def test_sealed_run_refuses_reply_and_restores_the_claimed_session(client):
    c, scheduled = client
    recorder, _, before = _conversation(c)
    recorder.complete({'validation': {'trip': {'id': 'done'}}})  # sealed terminal
    response = c.post('/api/v1/planner/conversations/' + recorder.id + '/reply', json=_body())
    assert response.status_code == 409, response.text
    row = load_session(recorder.id)
    assert row['status'] == 'needs_input' and row['version'] == 1
    # The reply is released: no consumed request id, no spent turn.
    assert 'lastRequestId' not in row['state'] and row['state']['replies'] == 0
    assert row['state']['turns'] == before['turns']
    with SessionLocal() as session:
        run = session.get(Run, recorder.id)
        assert run.generation == 0
        assert json.loads(run.result)['status'] == 'completed'
    assert 'resumed' not in [e.phase for e in events_for(recorder.id)]
    assert scheduled == []
    # The same request can be retried immediately (it is not a stale version).
    assert c.post('/api/v1/planner/conversations/' + recorder.id + '/reply',
                  json=_body()).status_code == 409


def test_any_restart_refusal_releases_the_reply(client, monkeypatch):
    c, scheduled = client
    recorder, _, _ = _conversation(c)
    recorder.finish('needs_input', {'question': '哪天出发？'})
    monkeypatch.setattr(RunRecorder, 'restart', lambda self: False)
    response = c.post('/api/v1/planner/conversations/' + recorder.id + '/reply', json=_body())
    assert response.status_code == 409, response.text
    row = load_session(recorder.id)
    assert row['status'] == 'needs_input' and row['version'] == 1
    assert 'lastRequestId' not in row['state'] and row['state']['replies'] == 0
    assert scheduled == []
    with SessionLocal() as session:
        assert session.get(Run, recorder.id).generation == 0


def test_idempotent_replay_does_not_restart_or_schedule_again(client):
    c, scheduled = client
    recorder, _, _ = _conversation(c)
    recorder.finish('needs_input', {'question': '哪天出发？'})
    body = _body()
    first = c.post('/api/v1/planner/conversations/' + recorder.id + '/reply', json=body)
    assert first.status_code == 200
    replay = c.post('/api/v1/planner/conversations/' + recorder.id + '/reply', json=body)
    assert replay.status_code == 200 and replay.json()['idempotent'] is True
    assert replay.json()['version'] == 2
    assert len(scheduled) == 1
    with SessionLocal() as session:
        assert session.get(Run, recorder.id).generation == 1

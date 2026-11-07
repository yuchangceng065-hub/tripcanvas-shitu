"""Regressions for the 2026-11-07 full-project audit fixes.

Each test maps to one concrete defect found by the audit. They are ordinary
passing tests, never xfails: the corrected behaviour is the contract.
"""
import asyncio
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import app
from backend.db import PlanningSession, Run, RunEvent, SessionLocal


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- 1. AG-UI scrubbing must keep billing telemetry ------------------------

def test_agui_keeps_token_usage_fields():
    from backend.agui_events import to_agui_event
    from backend.model_metrics import empty_metrics

    usage = empty_metrics()
    usage.update({'promptTokens': 1200, 'completionTokens': 340,
                  'totalTokens': 1540, 'tokensComplete': True})
    event = to_agui_event('model', 'ok', {'usage': usage}, 1, 'run-1')
    kept = event['detail']['usage']
    for field in ('promptTokens', 'completionTokens', 'totalTokens', 'tokensComplete'):
        assert kept.get(field) == usage[field], f'{field} was wrongly scrubbed'
    assert kept.get('providerRequests') == usage.get('providerRequests')


def test_agui_still_scrubs_real_credentials():
    from backend.agui_events import to_agui_event

    detail = {'apiKey': 'sk-secret', 'accessToken': 'bearer-x',
              'nested': {'password': 'hunter2', 'prompt': 'system instructions'},
              'totalTokens': 10}
    cleaned = to_agui_event('model', 'ok', detail, 2, 'run-2')['detail']
    assert 'apiKey' not in cleaned
    assert 'accessToken' not in cleaned
    assert 'password' not in cleaned['nested']
    assert 'prompt' not in cleaned['nested']
    assert cleaned['totalTokens'] == 10


# --- 2. Malformed provider payload is a generation error, not a crash ------

@pytest.mark.parametrize('payload', [
    {},                       # no choices
    {'choices': []},          # empty choices
    {'choices': [{}]},        # no message
    {'choices': [{'message': {}}]},  # no content
    ['not-a-dict'],           # wrong top-level type
])
def test_malformed_provider_payload_raises_value_error(monkeypatch, payload):
    from backend import model_gateway

    monkeypatch.setenv('PLANNER_MODE', 'model')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://provider.example/v1')
    monkeypatch.setenv('MODEL_NAME', 'test-model')
    monkeypatch.setattr(model_gateway, 'model_api_key', lambda: 'test-key')
    monkeypatch.setattr(model_gateway, 'model_options', lambda base, model: {})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(model_gateway.httpx, 'AsyncClient', FakeClient)
    with pytest.raises(ValueError, match='结构异常'):
        asyncio.run(model_gateway.model_json_messages([{'role': 'user', 'content': 'hi'}]))


def test_well_formed_provider_payload_still_parses(monkeypatch):
    from backend import model_gateway

    monkeypatch.setenv('PLANNER_MODE', 'model')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://provider.example/v1')
    monkeypatch.setenv('MODEL_NAME', 'test-model')
    monkeypatch.setattr(model_gateway, 'model_api_key', lambda: 'test-key')
    monkeypatch.setattr(model_gateway, 'model_options', lambda base, model: {})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '{"action":"need_input"}'},
                                 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 5}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(model_gateway.httpx, 'AsyncClient', FakeClient)
    assert asyncio.run(model_gateway.model_json_messages([{'role': 'user', 'content': 'hi'}])) == {
        'action': 'need_input'}


# --- 3. Deleting a trip removes its run events and planning sessions ------

def test_delete_trip_cleans_run_events_and_sessions(client):
    from backend.runlog import RunRecorder

    name = 'user_' + uuid.uuid4().hex[:8]
    assert client.post('/api/v1/auth/register',
                       json={'username': name, 'password': 'correct-horse-824!'}).status_code == 200
    trip_id = 'audit-trip-' + uuid.uuid4().hex[:8]
    payload = {'id': trip_id, 'title': '清理验收', 'city': '杭州', 'people': 2,
               'mode': 'walk', 'revision': 1,
               'days': [{'date': '2026-11-07', 'start': 570, 'end': 1110,
                         'activities': [{'id': 'a', 'placeId': 'quyuan',
                                         'duration': 90, 'lock': 'none'}]}]}
    assert client.put('/api/v1/trips/' + trip_id, json={'trip': payload}).status_code == 200

    recorder = RunRecorder(name, trip_id, 1, 'agent')
    recorder.event('model', detail={'step': 1})
    with SessionLocal.begin() as session:
        session.add(PlanningSession(id=recorder.id, owner=name, version=1,
                                    status='running', state='{}', updated=0.0))
    with SessionLocal() as session:
        assert session.get(Run, recorder.id) is not None
        assert session.scalar(select(func.count(RunEvent.id)).where(
            RunEvent.run_id == recorder.id)) == 1

    assert client.delete('/api/v1/trips/' + trip_id).status_code == 200
    with SessionLocal() as session:
        assert session.get(Run, recorder.id) is None
        assert session.get(PlanningSession, recorder.id) is None
        assert session.scalar(select(func.count(RunEvent.id)).where(
            RunEvent.run_id == recorder.id)) == 0


# --- 4. Startup recovery tolerates compact JSON (no space after colon) ----

def test_compact_running_result_is_recognised():
    """The old LIKE pattern only matched '\"status\": \"running\"'."""
    from backend.runlog import _run_status

    spaced = Run(result=json.dumps({'kind': 'agent', 'status': 'running'}))
    compact = Run(result=json.dumps({'kind': 'agent', 'status': 'running'},
                                    separators=(',', ':')))
    assert _run_status(spaced) == 'running'
    assert _run_status(compact) == 'running'
    assert _run_status(Run(result='not-json')) is None
    assert _run_status(Run(result='null')) is None


# --- 5. Trajectory evaluator: non-empty failure reason + refusal rate -----

def test_sequence_check_reports_reason_when_seq_starts_at_zero():
    from backend.trajectory_eval import _process_checks

    case = {'events': [{'seq': 0, 'phase': 'started', 'status': 'ok', 'detail': {}}],
            'expect': {'terminal': 'completed', 'allowedTools': [],
                       'allowedPlaceIds': [], 'requiredPlaceIds': []},
            'result': {'status': 'completed'}}
    checks = {c['name']: c for c in _process_checks(case)}
    failed = checks['event_sequence_monotonic']
    assert failed['passed'] is False
    assert failed['reason'], 'a failed check must have a diagnostic reason'


def test_refusal_rate_counts_all_non_success_terminals():
    from backend.trajectory_eval import evaluate_suite

    base = {'events': [], 'expect': {'terminal': 'completed', 'allowedTools': [],
                                     'allowedPlaceIds': [], 'requiredPlaceIds': []},
            'result': {'status': 'completed'}, 'spec': {},
            'title': '', 'split': 'audit', 'expectedPass': None}
    cases = []
    for index, status in enumerate(('completed', 'failed', 'incomplete', 'cancelled',
                                    'gave_up', 'interrupted')):
        case = dict(base)
        case['id'] = f'case-{index}'
        case['expect'] = {**base['expect'], 'terminal': status}
        case['result'] = {'status': status}
        cases.append(case)
    report = evaluate_suite(cases, labeled=False)
    assert report['refusalRate'] == pytest.approx(round(5 / 6, 3)), report


# --- 6. Real-task export tolerates a plan without `days` ------------------

def test_real_task_export_handles_plan_without_days():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        'evaluate_real_tasks_audit', root / 'scripts' / 'evaluate_real_tasks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # A captured result with a plan dict that lacks 'days' must not raise.
    spec = {'terminal': 'completed', 'allowedTools': [], 'allowedPlaceIds': [],
            'requiredPlaceIds': [], 'expectProposal': True, 'written': False}
    task = {
        'id': 'R99', 'title': '无 days 的采集', 'spec': spec,
        'capture': {'runAlias': 'R99', 'events': [],
                    'result': {'status': 'completed', 'plan': {'title': 'no-days'},
                               'written': False, 'confirmation': None}},
    }
    case = module._case(task)
    assert case['id'] == 'R99'
    assert case['result']['written'] is False
    # A plan without `days` must degrade to "no plan evidence", not crash.
    assert case['result']['plan'] is None


# --- 7. walking_km: missing segment data must be "unknown", not "0 km" ----

def test_amap_route_without_segments_is_unknown():
    from backend.planning import walking_km

    assert walking_km({'source': 'amap', 'legs': [{'minutes': 10}]}, 'drive') is None
    assert walking_km({'source': 'amap', 'legs': []}, 'drive') is None
    # Once segments are present, a purely driving leg is genuinely 0 km.
    assert walking_km({'source': 'amap', 'legs': [
        {'minutes': 10, 'segments': [{'mode': 'drive', 'distance': 3.0}]}]}, 'drive') == 0.0
    assert walking_km({'source': 'amap', 'legs': [
        {'minutes': 10, 'segments': [{'mode': 'walk', 'distance': 1.5},
                                     {'mode': 'drive', 'distance': 3.0}]}]}, 'drive') == 1.5


# --- 8. confirm() honours the generation fence -----------------------------


def test_confirm_refuses_a_stale_generation():
    from backend.runlog import RunRecorder

    rec = RunRecorder('owner-audit-confirm', 'trip-audit', 1, 'agent')
    rec.finish('completed', {'validation': {'ok': True}})
    # Simulate a recorder restored from an older activation.
    stale = RunRecorder.restore(rec.id)
    stale.generation = rec.generation - 1
    assert stale.confirm({'tripId': 'trip-audit', 'revision': 2}) is False
    with SessionLocal() as session:
        run = session.get(Run, rec.id)
        assert run.applied == 0
    # The current generation can still write exactly one marker.
    assert rec.confirm({'tripId': 'trip-audit', 'revision': 2}) is True
    assert rec.confirm({'tripId': 'trip-audit', 'revision': 2}) is True  # idempotent

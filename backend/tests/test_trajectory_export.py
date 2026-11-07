"""Regression tests for exporting real persisted runs into trajectory cases."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.trajectory_eval import evaluate_suite, evaluate_trajectory
from backend.trajectory_export import (export_case, export_cases,
                                       export_case_from_payload, load_run, spec_template)


@pytest.fixture
def client():
    from backend.app import app, attempts
    attempts.clear()
    with TestClient(app) as c:
        yield c


SPEC = {
    'terminal': 'completed',
    'allowedPlaceIds': ['quyuan', 'museum', 'station'],
    'requiredPlaceIds': ['quyuan', 'museum'],
    'expectProposal': True,
    'days': 1,
}


def make_run(written=False, places=('quyuan', 'museum')):
    from backend.db import SessionLocal, TripRow
    from backend.runlog import RunRecorder

    trip_id = 'trip-' + uuid.uuid4().hex[:10]
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], trip_id, 1, 'agent')
    rec.event('model', detail={'step': 1, 'action': 'route_day'})
    rec.event('tool_call', detail={'step': 1, 'tool': 'route_day',
                                   'args': {'days': [list(places)]}})
    rec.event('tool_result', detail={'step': 1, 'ok': True, 'summary': '道路可用'})
    rec.event('model', detail={'step': 2, 'action': 'propose_plan'})
    rec.event('tool_call', detail={'step': 2, 'tool': 'propose_plan',
                                   'args': {'days': [list(places)], 'summary': '方案'}})
    rec.event('tool_result', detail={'step': 2, 'ok': True, 'summary': '已核验'})
    trip = {'id': trip_id, 'title': '导出验证', 'days': [
        {'date': '2026-11-14', 'activities': [{'placeId': place} for place in places]},
    ]}
    rec.complete({'validation': {'trip': trip, 'issues': [], 'includedIds': list(places),
                                 'omittedIds': []}})
    if written:
        with SessionLocal.begin() as session:
            session.add(TripRow(id=trip_id, owner=rec.id, revision=1,
                                snapshot=json.dumps(trip)))
    return rec.id, trip_id


def test_export_uses_persisted_events_and_judges_clean_run(client):
    run_id, _ = make_run()
    case = export_case(run_id, SPEC)
    assert case['split'] == 'observed' and case['expectedPass'] is None
    assert [event['seq'] for event in case['events']] == [1, 2, 3, 4, 5, 6, 7]
    assert case['events'][-1]['phase'] == 'completed'
    assert case['result']['written'] is False
    assert case['result']['plan']['days'][0]['activities'][1]['placeId'] == 'museum'
    assert case['expect']['allowedPlaceIds'] == SPEC['allowedPlaceIds']
    assert case['provenance']['expectationSource'].startswith('caller-provided')
    row = evaluate_trajectory(case)
    assert row['verdict'] == 'both_pass' and row['passed']


def test_write_status_comes_from_the_trips_table(client):
    run_id, trip_id = make_run(written=True)
    case = export_case(run_id, SPEC)
    assert case['result']['written'] is True
    assert case['provenance']['writeCheckedId'] == trip_id


def test_export_detects_unconfirmed_place_from_real_events(client):
    run_id, _ = make_run()
    case = export_case(run_id, SPEC)
    # Simulate a stored event that referenced a place outside the confirmed set.
    for event in case['events']:
        if event['phase'] == 'tool_call' and event['detail'].get('tool') == 'route_day':
            event['detail']['args']['days'] = [['quyuan', 'museum', 'silk']]
    row = evaluate_trajectory(case)
    assert row['outcomePass'] is True and row['processPass'] is False
    assert row['verdict'] == 'result_only'


def test_expectations_must_come_from_the_caller(client):
    run_id, _ = make_run()
    with pytest.raises(ValueError, match='pre-run inputs'):
        export_case(run_id, {key: value for key, value in SPEC.items()
                             if key != 'allowedPlaceIds'})
    with pytest.raises(ValueError, match='run not found'):
        load_run('missing-run-id')


def test_observed_mode_does_not_pretend_to_validate_the_evaluator(client):
    run_id, _ = make_run()
    cases = export_cases({'': {'maxSteps': 6, 'maxToolCalls': 6},
                          run_id: SPEC})
    report = evaluate_suite(cases, labeled=False)
    assert report['mode'] == 'observed'
    assert report['total'] == 1 and report['passed'] == 1
    assert report['evaluatorValid'] is None and report['agreement'] is None
    assert report['falseAccepts'] == [] and report['falseRejects'] == []


def test_spec_template_and_bad_event_history(client):
    template = spec_template()
    assert '<RUN_ID>' in template['runs'] and template['defaults']['maxSteps'] == 6
    run_id, _ = make_run()
    from backend.db import RunEvent, SessionLocal
    from backend.runlog import encode_detail
    with SessionLocal.begin() as session:
        session.add(RunEvent(id=str(uuid.uuid4()), run_id=run_id, seq=99,
                             phase='tool_result', status='ok', detail='{broken',
                             created=0.0))
    data = load_run(run_id)
    assert any(event['detail'].get('truncated') for event in data['events'])
    assert isinstance(encode_detail({'ok': True}), str)


LIVE_SPEC = {
    'terminal': 'needs_input',
    'allowedTools': ['route_day', 'validate_plan', 'propose_plan'],
    'allowedPlaceIds': ['quyuan', 'museum', 'station'],
    'requiredPlaceIds': [],
    'expectProposal': False,
    'maxSteps': 6,
    'maxToolCalls': 6,
    'written': False,
}


def live_payload(mcp_ok=True, duplicate_query=False, pause_status='needs_input'):
    queries = ['曲院风荷', '杭州东站']
    if duplicate_query:
        queries.append('曲院风荷')
    events = [
        {'seq': 1, 'phase': 'graph_node', 'status': 'ok', 'detail': {'node': 'understand'}},
        {'seq': 2, 'phase': 'understanding', 'status': 'ok', 'detail': {'attempt': 1}},
    ]
    seq = 3
    for query in queries:
        events.append({'seq': seq, 'phase': 'place_search', 'status': 'ok',
                       'detail': {'count': seq, 'query': query}})
        seq += 1
        events.append({'seq': seq, 'phase': 'mcp_call', 'status': 'ok' if mcp_ok else 'error',
                       'detail': {'tool': 'search_places', 'ok': mcp_ok,
                                  'durationMs': 1000, 'transport': 'streamable-http'}})
        seq += 1
    events += [
        {'seq': seq, 'phase': 'search_refine', 'status': 'ok', 'detail': {'round': 1, 'mentions': 2}},
        {'seq': seq + 1, 'phase': 'clarification', 'status': 'ok', 'detail': {'ready': False}},
        {'seq': seq + 2, 'phase': 'needs_input', 'status': 'ok', 'detail': {'steps': seq + 2}},
        {'seq': seq + 3, 'phase': 'graph_pause', 'status': 'ok',
         'detail': {'status': pause_status, 'engine': 'langgraph'}},
    ]
    return {'status': 'needs_input', 'runId': 'live-run', 'kind': 'conversation',
            'modelUsage': {'totalTokens': 2479, 'tokensComplete': True}, 'events': events}


def test_payload_export_handles_real_pause_terminated_run():
    case = export_case_from_payload('live-run-1', live_payload(), LIVE_SPEC)
    assert case['split'] == 'observed' and case['result']['written'] is False
    assert case['events'][-1]['phase'] == 'graph_pause'
    assert case['provenance']['source'] == 'live-api-payload'
    row = evaluate_trajectory(case)
    assert row['verdict'] == 'both_pass' and row['passed'], row['failedChecks']
    assert row['outcomeReasons'] == []


def test_payload_export_detects_failed_mcp_and_duplicate_search():
    case = export_case_from_payload('live-run-2',
                                    live_payload(mcp_ok=False, duplicate_query=True), LIVE_SPEC)
    row = evaluate_trajectory(case)
    assert row['passed'] is False
    assert 'mcp_call_results_ok' in row['failedChecks']
    assert 'no_duplicate_search_query' in row['failedChecks']


def test_trailing_pause_status_must_match_reported_terminal():
    case = export_case_from_payload('live-run-3',
                                    live_payload(pause_status='completed'), LIVE_SPEC)
    row = evaluate_trajectory(case)
    assert 'trailing_pause_matches_status' in row['failedChecks']


def test_payload_export_requires_explicit_write_status():
    spec = {key: value for key, value in LIVE_SPEC.items() if key != 'written'}
    with pytest.raises(ValueError, match='written'):
        export_case_from_payload('live-run-4', live_payload(), spec)


def test_mid_run_graph_pause_does_not_break_a_finished_run(client):
    run_id, _ = make_run()
    case = export_case(run_id, SPEC)
    case['events'].insert(2, {'seq': 99, 'phase': 'graph_pause', 'status': 'ok',
                              'detail': {'status': 'needs_input'}})
    case['events'] = [dict(event, seq=index + 1)
                      for index, event in enumerate(case['events'])]
    row = evaluate_trajectory(case)
    assert 'trailing_pause_matches_status' not in row['failedChecks']
    assert row['passed'] is True


def test_real_confirm_endpoint_event_satisfies_observed_write_check(client, monkeypatch):
    """Full local chain: completed run -> /planner/confirm -> export -> oracle."""
    from sqlalchemy import select
    from backend.app import app
    from backend.db import User, SessionLocal
    from backend.runlog import RunRecorder, events_for

    async def roads(body):
        return {'source': 'osrm', 'legs': [{'minutes': 10, 'distance': .6}
                                          for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router, 'route', roads)
    name = 'capture_' + uuid.uuid4().hex[:8]
    assert client.post('/api/v1/auth/register',
                       json={'username': name, 'password': 'correct-horse-824!'}).status_code == 200
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.username == name)).id

    trip_id = 'trip-' + uuid.uuid4().hex[:10]
    trip = {'id': trip_id, 'title': '导出验证', 'city': '杭州', 'people': 2,
            'mode': 'walk', 'revision': 1, 'days': [{'date': '2026-11-14',
            'start': 570, 'end': 1110, 'activities': [
                {'id': 'a', 'placeId': 'quyuan', 'duration': 90, 'lock': 'keep'},
                {'id': 'b', 'placeId': 'museum', 'duration': 90, 'lock': 'keep'}]}]}
    rec = RunRecorder(owner, trip_id, 0, 'agent')
    rec.event('model', detail={'step': 1, 'action': 'route_day'})
    rec.event('tool_call', detail={'step': 1, 'tool': 'route_day',
                                   'args': {'days': [['quyuan', 'museum']]}})
    rec.event('tool_result', detail={'step': 1, 'ok': True, 'summary': '道路可用'})
    rec.event('model', detail={'step': 2, 'action': 'propose_plan'})
    rec.event('tool_call', detail={'step': 2, 'tool': 'propose_plan',
                                   'args': {'days': [['quyuan', 'museum']]}})
    rec.event('tool_result', detail={'step': 2, 'ok': True, 'summary': '已核验'})
    rec.complete({'validation': {'trip': trip}})

    confirmed = client.post('/api/v1/planner/confirm',
                            json={'trip': trip, 'baseRevision': 0, 'runId': rec.id})
    assert confirmed.status_code == 200, confirmed.text
    assert [event.phase for event in events_for(rec.id)][-1] == 'confirmed'

    spec = {**SPEC, 'allowedPlaceIds': ['quyuan', 'museum'],
            'requiredPlaceIds': ['quyuan', 'museum']}
    case = export_case(rec.id, spec)
    assert case['result']['written'] is True
    assert case['result']['confirmation'] is None  # evidence is the real event
    row = evaluate_trajectory(case)
    assert row['passed'] is True, row['failedChecks']

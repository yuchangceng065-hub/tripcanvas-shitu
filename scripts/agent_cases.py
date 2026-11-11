"""Synthetic model and road fixtures exercising the real conversation API."""
import time
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import app, attempts
from backend.evaluation import audit_option
from backend.places import POIS

CASES = {
    'restart_and_repair': {'terminal': 'completed', 'roadMinutes': 80},
    'invalid_candidate': {'terminal': 'completed', 'roadMinutes': 10},
    'upstream_unavailable': {'terminal': 'gave_up', 'roadMinutes': 10},
}


def response_json(response):
    response.raise_for_status()
    return response.json()


def wait_for_run(client, run_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        run = response_json(client.get('/api/v1/runs/' + run_id))
        if run['status'] != 'running':
            return run
        time.sleep(.02)
    raise TimeoutError('Agent fixture did not reach a stable state')


def run_case(case_id):
    case = CASES[case_id]
    model_calls = 0
    road_calls = 0
    errors = []

    async def extract(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return {
            'city': '杭州', 'startDate': '2026-11-14', 'days': 1, 'people': 2,
            'mode': None if model_calls == 1 else 'walk',
            'start': 780, 'end': 1110, 'returnBy': 1020, 'walkBudgetKm': None,
            'pace': 'relaxed', 'interests': [], 'notes': [],
            'mentions': [{'text': '曲院风荷', 'role': 'visit', 'required': True},
                         {'text': '浙江省博物馆孤山馆区', 'role': 'visit', 'required': False},
                         {'text': '杭州东站', 'role': 'destination', 'required': True}],
        }

    decisions = [
        {'action': 'validate_plan', 'args': {'days': [['quyuan', 'museum']], 'summary': '核验草案'}},
    ]
    if case_id == 'restart_and_repair':
        decisions.append({'action': 'repair_plan', 'args': {'seedDays': [['quyuan', 'museum']]}})
    elif case_id == 'invalid_candidate':
        decisions[0]['args']['days'][0].append('station')
        decisions.append({'action': 'propose_plan', 'args': {'days': [['quyuan', 'museum']], 'summary': '保留返程'}})
    else:
        decisions.append({'action': 'give_up', 'args': {'reason': '地图服务暂不可用'}})

    async def choose(*args, **kwargs):
        nonlocal model_calls
        model_calls += 1
        return decisions.pop(0)

    async def search(query):
        key = 'station' if '站' in query else 'museum' if '博物馆' in query else 'quyuan'
        return [POIS[key]]

    def route_fixture(count):
        return {'source': 'osrm', 'fixture': True, 'legs': [
            {'minutes': case['roadMinutes'], 'distance': .6} for _ in range(count - 1)
        ]}

    async def route(body):
        nonlocal road_calls
        road_calls += 1
        if case_id == 'upstream_unavailable':
            raise HTTPException(503, '夹具：地图服务暂不可用')
        return route_fixture(len(body.placeIds))

    def services(stack):
        client = stack.enter_context(TestClient(app))
        stack.enter_context(patch.object(app.state.search, 'search', search))
        stack.enter_context(patch.object(app.state.router, 'route', route))
        return client

    def reply(client, run, **answer):
        body = {'version': run['conversation']['version'], 'requestId': uuid.uuid4().hex, **answer}
        response_json(client.post('/api/v1/planner/conversations/' + run_id + '/reply', json=body))
        return wait_for_run(client, run_id)

    attempts.clear()
    started = time.monotonic()
    with ExitStack() as patches:
        patches.enter_context(patch.dict('os.environ', {
            'PLANNER_MODE': 'auto', 'MODEL_BASE_URL': 'https://fixture.invalid/v1',
            'MODEL_NAME': 'scripted-agent', 'MODEL_API_KEY': 'fixture-only',
            'GROQ_API_KEY': '', 'AMAP_WEB_KEY': '',
        }))
        patches.enter_context(patch('backend.conversation.model_json_messages', extract))
        patches.enter_context(patch('backend.agent.model_json_messages', choose))
        with ExitStack() as first:
            client = services(first)
            auth = response_json(client.post('/api/v1/auth/register', json={
                'username': 'agent_qa_' + uuid.uuid4().hex[:10], 'password': 'isolated-agent-fixture',
            }))
            headers = {'Authorization': 'Bearer ' + auth['access_token']}
            run_id = response_json(client.post('/api/v1/planner/conversations', json={
                'title': 'Agent 评测行程',
                'text': '2026-11-14 在杭州玩一天，13:00开始，曲院风荷必去，浙江省博物馆孤山馆区有空再去，17:00前到杭州东站。',
            }))['runId']
            paused = wait_for_run(client, run_id)
            if paused['status'] != 'needs_input' or 'mode' not in paused['conversation']['missing']:
                raise AssertionError('Agent should pause for missing transport mode')

        # A new FastAPI lifespan opens a new checkpointer connection to the
        # same store. No in-memory LangGraph object survives the restart.
        with ExitStack() as second:
            client = services(second)
            client.headers.update(headers)
            choices = {mention['key']: mention['candidates'][0]['id']
                       for mention in paused['conversation']['mentions']}
            review = reply(client, paused, text='两个人，步行。', choices=choices)
            if review['status'] != 'needs_confirmation':
                raise AssertionError('Agent should pause for the confirmed requirements')
            path = '/api/v1/runs/' + run_id + '/events'
            with client.stream('GET', path) as stream:
                stream.raise_for_status()
                cursor = next(int(line[4:]) for line in stream.iter_lines() if line.startswith('id: '))
            run = reply(client, review, confirm=True)
            stream = client.get(path, headers={'Last-Event-ID': str(cursor)})
            stream.raise_for_status()
            replayed = [int(line[4:]) for line in stream.text.splitlines() if line.startswith('id: ')]
            if replayed != [event['seq'] for event in run['events'] if event['seq'] > cursor]:
                errors.append('sse_resume_cursor')
            if run['status'] != case['terminal']:
                errors.append('terminal_status')
            if run.get('engine') != 'langgraph':
                errors.append('graph_engine')
            events = run['events']
            if sum(event['phase'] == 'graph_resume' for event in events) != 2:
                errors.append('checkpoint_resume')
            mcp_events = [event for event in events if event['phase'] == 'mcp_call']
            if not any(event['detail']['tool'] == 'search_places' for event in mcp_events):
                errors.append('search_not_mcp')
            if not any(event['detail']['tool'] == 'validate_plan' for event in mcp_events):
                errors.append('validation_not_mcp')
            if case_id != 'restart_and_repair' and not any(event['status'] == 'error' for event in mcp_events):
                errors.append('missing_tool_failure')
            if response_json(client.get('/api/v1/trips')):
                errors.append('unconfirmed_write')

            if run['status'] == 'completed':
                plan = run['validation']
                trip = plan['trip']
                routes = {index: route_fixture(len(day['activities']))
                          for index, day in enumerate(trip['days']) if len(day['activities']) > 1}
                errors.extend(audit_option({**plan, 'routes': routes}, {
                    'ids': ['quyuan', 'museum'], 'optional': ['museum'], 'days': 1,
                    'start': 780, 'destination': 'station', 'returnBy': 1020,
                }, route_fixture=True))
                if case_id == 'restart_and_repair' and not any(
                    event['detail']['tool'] == 'repair_plan' and event['status'] == 'ok'
                    for event in mcp_events
                ):
                    errors.append('repair_not_executed')
                body = {'trip': trip, 'baseRevision': 0, 'runId': run_id}
                for expected_replay in (False, True):
                    confirmation = response_json(client.post('/api/v1/planner/confirm', json=body))
                    if confirmation.get('idempotent') is not expected_replay:
                        errors.append('confirmation_replay')
                recorded = response_json(client.get('/api/v1/runs/' + run_id))
                confirmed = [event for event in recorded['events'] if event['phase'] == 'confirmed']
                if len(confirmed) != 1 or confirmed[0]['detail'].get('tripId') != trip['id']:
                    errors.append('confirmation_trace')
                versions = response_json(client.get('/api/v1/trips/' + trip['id'] + '/versions'))
                if len(versions) != 1:
                    errors.append('duplicate_versions')

    # The trace attached to the evaluation contains no inputs from user accounts.
    trace = [
        {'phase': event['phase'], 'status': event['status'], **{
            key: value for key, value in event['detail'].items()
            if key in ('node', 'tool', 'transport', 'durationMs', 'action', 'step')
        }}
        for event in events if event['phase'] in ('graph_node', 'graph_pause', 'graph_resume', 'model', 'mcp_call')
    ]
    return {'case': case_id, 'status': run['status'], 'errors': sorted(set(errors)),
            'modelFixtureCalls': model_calls, 'roadFixtureCalls': road_calls,
            'elapsedMs': round((time.monotonic() - started) * 1000), 'trace': trace}

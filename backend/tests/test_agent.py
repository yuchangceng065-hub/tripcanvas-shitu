import time
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.app import app


def register(client):
    r=client.post('/api/v1/auth/register',json={'username':'agent_'+uuid.uuid4().hex[:8],'password':'correct-horse-824!'})
    assert r.status_code==200,r.text


def body(days=1):
    return {'title':'Agent 测试','requirements':{'city':'杭州','startDate':'2026-11-14','days':days,'people':2,
            'mode':'walk','start':570,'end':1110,'pace':'balanced','interests':[]},
            'candidateIds':['museum','quyuan'],'optionalIds':[],'useModel':False}


def fast_roads(request):
    return {'source':'osrm','legs':[{'minutes':10,'distance':.6} for _ in request.placeIds[1:]]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('PLAN_AGENT_DAILY_LIMIT','50')
    monkeypatch.setenv('PLANNER_MODE','auto')
    monkeypatch.setenv('MODEL_BASE_URL','https://example.invalid/v1')
    monkeypatch.setenv('MODEL_NAME','glm-4.7-flash')
    monkeypatch.setenv('MODEL_API_KEY','test-key')
    with TestClient(app) as c:yield c


def scripted(monkeypatch, actions):
    async def fake(messages,**kwargs):
        assert actions, 'agent requested more steps than scripted'
        return actions.pop(0)
    monkeypatch.setattr('backend.agent.model_json_messages',fake)


def wait_run(client,run_id,timeout=10):
    deadline=time.time()+timeout
    while time.time()<deadline:
        data=client.get('/api/v1/runs/'+run_id).json()
        if data.get('status') not in ('running',None):return data
        time.sleep(0.05)
    raise AssertionError('run did not finish in time')


def test_agent_completes_with_tools_and_confirms(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    scripted(monkeypatch,[
        {'action':'route_day','args':{'placeIds':['museum','quyuan']}},
        {'action':'propose_plan','args':{'days':[['museum','quyuan']],'summary':'两站慢走'}},
    ])
    started=client.post('/api/v1/planner/agent',json=body())
    assert started.status_code==200,started.text
    assert started.json()['status']=='running'
    run=wait_run(client,started.json()['runId'])
    assert run['status']=='completed' and run['modelSteps']==2
    assert run['modelUsage'] is not None
    trip=run['validation']['trip']
    assert [a['placeId'] for d in trip['days'] for a in d['activities']]==['museum','quyuan']
    phases=[event['phase'] for event in run['events']]
    assert phases==['model','tool_call','tool_result','model','tool_call','tool_result','completed']
    sse=client.get('/api/v1/runs/'+run['runId']+'/events')
    assert 'event: tool_call' in sse.text and 'event: completed' in sse.text
    confirmed=client.post('/api/v1/planner/confirm',json={'trip':trip,'baseRevision':0})
    assert confirmed.status_code==200,confirmed.text


def test_agent_stops_at_step_cap(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    async def fake(messages,**kwargs):return {'action':'route_day','args':{'placeIds':['museum','quyuan']}}
    monkeypatch.setattr('backend.agent.model_json_messages',fake)
    started=client.post('/api/v1/planner/agent',json=body()).json()
    run=wait_run(client,started['runId'])
    assert run['status']=='incomplete' and '6' in run['message']
    assert sum(1 for event in run['events'] if event['phase']=='model')==6


def test_agent_reports_tool_errors_then_gives_up(client,monkeypatch):
    register(client)
    scripted(monkeypatch,[
        {'action':'route_day','args':{'placeIds':['invented']}},
        {'action':'give_up','args':{'reason':'候选里没有这个地点'}},
    ])
    started=client.post('/api/v1/planner/agent',json=body()).json()
    run=wait_run(client,started['runId'])
    assert run['status']=='gave_up'
    errors=[event for event in run['events'] if event['status']=='error']
    assert errors and '未知地点' in errors[0]['detail']['error']


def test_agent_requires_a_model(client,monkeypatch):
    register(client)
    monkeypatch.setenv('PLANNER_MODE','rules')
    response=client.post('/api/v1/planner/agent',json=body())
    assert response.status_code==503 and 'Agent' in response.json()['detail']

import pytest
from fastapi.testclient import TestClient
from backend.app import app


def register(client):
    import uuid
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
    with TestClient(app) as c:yield c


def scripted(monkeypatch, actions):
    async def fake(messages,**kwargs):
        assert actions, 'agent requested more steps than scripted'
        return actions.pop(0)
    monkeypatch.setattr('backend.agent.model_json_messages',fake)


def test_agent_completes_with_tools_and_confirms(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    scripted(monkeypatch,[
        {'action':'route_day','args':{'placeIds':['museum','quyuan']}},
        {'action':'propose_plan','args':{'days':[['museum','quyuan']],'summary':'两站慢走'}},
    ])
    response=client.post('/api/v1/planner/agent',json=body())
    assert response.status_code==200,response.text
    data=response.json()
    assert data['status']=='completed' and data['steps']==2 and data['runId']
    trip=data['plan']['trip'];assert [a['placeId'] for d in trip['days'] for a in d['activities']]==['museum','quyuan']
    phases=[event['phase'] for event in client.get('/api/v1/runs/'+data['runId']).json()['events']]
    assert phases==['model','tool_call','tool_result','model','tool_call','tool_result','completed']
    confirmed=client.post('/api/v1/planner/confirm',json={'trip':trip,'baseRevision':0})
    assert confirmed.status_code==200,confirmed.text


def test_agent_stops_at_step_cap(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    async def fake(messages,**kwargs):return {'action':'route_day','args':{'placeIds':['museum','quyuan']}}
    monkeypatch.setattr('backend.agent.model_json_messages',fake)
    response=client.post('/api/v1/planner/agent',json=body())
    assert response.status_code==200
    data=response.json();assert data['status']=='incomplete' and '6' in data['message']
    event=client.get('/api/v1/runs/'+data['runId']).json()
    assert event['status']=='failed'
    assert sum(1 for e in event['events'] if e['phase']=='model')==6


def test_agent_reports_tool_errors_then_gives_up(client,monkeypatch):
    register(client)
    scripted(monkeypatch,[
        {'action':'route_day','args':{'placeIds':['invented']}},
        {'action':'give_up','args':{'reason':'候选里没有这个地点'}},
    ])
    data=client.post('/api/v1/planner/agent',json=body()).json()
    assert data['status']=='gave_up'
    run=client.get('/api/v1/runs/'+data['runId']).json()
    errors=[e for e in run['events'] if e['status']=='error']
    assert errors and '未知地点' in errors[0]['detail']['error']


def test_agent_requires_a_model(client,monkeypatch):
    register(client)
    monkeypatch.setenv('PLANNER_MODE','rules')
    response=client.post('/api/v1/planner/agent',json=body())
    assert response.status_code==503 and 'Agent' in response.json()['detail']

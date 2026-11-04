import time
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.app import app,attempts


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
    attempts.clear()
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


@pytest.mark.parametrize('days,optional',[
    ([['museum','quyuan','silk']], []),
    ([['museum','quyuan','quyuan']], ['quyuan']),
    ([['museum','quyuan'],['quyuan']], ['quyuan']),
    ([['museum']], []),
])
def test_agent_rejects_unconfirmed_duplicate_and_missing_candidates(days,optional):
    import asyncio
    from backend.agent import Toolbox
    from backend.planning import AlternativesBody
    class Roads:
        async def route(self,request):raise AssertionError('Invalid candidates must fail before any provider request')
    data=body(len(days));data['optionalIds']=optional
    data=AlternativesBody.model_validate(data)
    with pytest.raises(ValueError):asyncio.run(Toolbox(data,Roads()).run('propose_plan',{'days':days}))


def test_agent_preserves_title_optional_choice_and_verified_timeline(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    scripted(monkeypatch,[{'action':'propose_plan','args':{'days':[['museum','quyuan']]}}])
    data=body();data['candidateIds'].append('silk');data['optionalIds']=['silk']
    data['requirements'].update(originPlaceId='station',destinationPlaceId='station',returnBy=1050)
    started=client.post('/api/v1/planner/agent',json=data).json();run=wait_run(client,started['runId'])
    assert run['status']=='completed',run
    plan=run['validation'];assert plan['trip']['title']==data['title'] and plan['omittedIds']==['silk']
    rows=plan['timeline'][0]['activities']
    assert [r['placeId'] for r in rows]==['station','museum','quyuan','station']
    assert rows[1]['start']==595 and rows[1]['travelMinutes']==10
    assert rows[-1]['start']==1050 and rows[-1]['lock']=='fixed'
    assert plan['metrics']['travelMinutes']==30
    assert client.get('/api/v1/trips/'+plan['trip']['id']).status_code==404


@pytest.mark.parametrize('change', ['unknown','duplicate','anchor-as-visit','not-enough'])
def test_agent_rejects_invalid_input_before_creating_background_run(client,change):
    register(client);data=body()
    if change=='unknown':data['candidateIds']=['not-confirmed']
    if change=='duplicate':data['candidateIds']=['museum','museum']
    if change=='anchor-as-visit':data['requirements']['originPlaceId']='museum'
    if change=='not-enough':data['requirements']['days']=3
    response=client.post('/api/v1/planner/agent',json=data)
    assert response.status_code==422


def test_run_events_remain_valid_json_after_bounding_and_recover_legacy_data(client):
    import json
    from backend.runlog import RunRecorder,events_for
    from backend.db import SessionLocal,RunEvent
    register(client)
    # Use the authenticated owner without retaining any credential in the report.
    from backend.db import User
    from sqlalchemy import select
    username=client.get('/api/v1/auth/me').json()['username']
    with SessionLocal() as session:owner=session.scalar(select(User).where(User.username==username)).id
    rec=RunRecorder(owner,'',0,'agent')
    rec.event('tool_result',status='error',detail={'step':2,'ok':False,'error':'错误说明'*2000,'summary':[{'nested':['x'*600]*15}]*20})
    rec.finish('failed',{'error':'test'})
    event=events_for(rec.id)[0];assert len(event.detail)<=4000 and json.loads(event.detail)['truncated']
    with SessionLocal.begin() as session:session.get(RunEvent,event.id).detail='{"old":"cut'
    result=client.get('/api/v1/runs/'+rec.id)
    assert result.status_code==200 and result.json()['events'][0]['detail']['truncated']
    sse=client.get('/api/v1/runs/'+rec.id+'/events',headers={'Last-Event-ID':'0'})
    blocks=[b for b in sse.text.split('\n\n') if 'event: tool_result' in b]
    payload=json.loads(next(line[6:] for line in blocks[0].splitlines() if line.startswith('data: ')))
    assert payload['status']=='error' and payload['detail']['truncated']


def test_event_resume_preflight_allows_last_event_id(client):
    response=client.options('/api/v1/runs/example/events',headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization,last-event-id'})
    assert response.status_code==200


def test_round_trip_return_deadline_checks_last_occurrence():
    import asyncio
    from backend.agent import Toolbox
    from backend.planning import AlternativesBody
    class SlowRoads:
        async def route(self,request):return {'source':'osrm','legs':[{'minutes':90,'distance':6} for _ in request.placeIds[1:]]}
    data=body();data['requirements'].update(originPlaceId='station',destinationPlaceId='station',returnBy=800)
    result=asyncio.run(Toolbox(AlternativesBody.model_validate(data),SlowRoads()).run('propose_plan',{'days':[['museum','quyuan']]}))
    assert not result['ok'] and any(i['code']=='return_deadline' for i in result['issues'])


@pytest.mark.parametrize('code,exception', [('json_validate_failed',ValueError),('output_parse_failed',ValueError),('invalid_request_error',Exception)])
def test_agent_gateway_only_repairs_explicit_json_generation_failure(client,monkeypatch,caplog,code,exception):
    import asyncio,httpx
    from fastapi import HTTPException
    from backend.model_gateway import model_json_messages
    from backend.model_metrics import current_metrics,empty_metrics
    calls=[];original=httpx.AsyncClient
    def provider(request):
        calls.append(request)
        return httpx.Response(400,json={'error':{'code':code,'failed_generation':'PRIVATE_OUTPUT_DO_NOT_LOG'}})
    monkeypatch.setattr('backend.model_gateway.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(provider)))
    metrics=empty_metrics();token=current_metrics.set(metrics)
    try:
        with pytest.raises(ValueError if exception is ValueError else HTTPException):asyncio.run(model_json_messages([{'role':'user','content':'JSON'}]))
    finally:current_metrics.reset(token)
    assert len(calls)==1 and metrics['providerRequests']==1 and not metrics['tokensComplete']
    assert 'PRIVATE_OUTPUT_DO_NOT_LOG' not in caplog.text


def test_agent_repairs_json_rejection_within_existing_step_budget(client,monkeypatch):
    register(client)
    async def roads(request):return fast_roads(request)
    monkeypatch.setattr(app.state.router,'route',roads)
    calls=[]
    async def model(messages,**kwargs):
        calls.append(messages[:])
        if len(calls)==1:raise ValueError('供应商未生成有效 JSON')
        return {'action':'propose_plan','args':{'days':[['museum','quyuan']]}}
    monkeypatch.setattr('backend.agent.model_json_messages',model)
    run_id=client.post('/api/v1/planner/agent',json=body()).json()['runId']
    run=wait_run(client,run_id)
    assert run['status']=='completed' and run['modelSteps']==2
    assert run['events'][0]['phase']=='model_error' and 'observation:' in calls[1][-1]['content']


@pytest.mark.parametrize('base,model,strict',[
    ('https://api.groq.com/openai/v1','openai/gpt-oss-20b',True),
    ('https://api.groq.com/openai/v1','other-model',False),
    ('https://api.groq.com.invalid/v1','openai/gpt-oss-20b',False),
])
def test_agent_strict_format_is_limited_to_supported_provider_models(client,monkeypatch,base,model,strict):
    import asyncio,json,httpx
    from backend.agent_schema import decision_format
    from backend.model_gateway import model_json_messages
    monkeypatch.setenv('MODEL_BASE_URL',base);monkeypatch.setenv('MODEL_NAME',model);monkeypatch.setenv('GROQ_API_KEY','test-key')
    original=httpx.AsyncClient;calls=[];schema=decision_format(['museum','quyuan'],['station'])
    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"action":"need_input","args":{"question":"日期？"}}'},'finish_reason':'stop'}]})
    monkeypatch.setattr('backend.model_gateway.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(provider)))
    asyncio.run(model_json_messages([{'role':'user','content':'JSON'}],response_format=schema))
    assert calls[0]['response_format']==(schema if strict else {'type':'json_object'})
    root=schema['json_schema']['schema'];args=root['properties']['args']
    keysets=[set(variant['required']) for variant in args['anyOf']]
    assert all(not left & right for i,left in enumerate(keysets) for right in keysets[i+1:])
    assert all(variant['additionalProperties'] is False and set(variant['required'])==set(variant['properties']) for variant in args['anyOf'])
    assert args['anyOf'][1]['properties']['days']['items']['items']['enum']==['museum','quyuan']
    assert args['anyOf'][0]['properties']['placeIds']['items']['enum']==['museum','quyuan','station']

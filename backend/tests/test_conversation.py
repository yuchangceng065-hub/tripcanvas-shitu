import copy,json,time,uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app import app,attempts
from backend.places import POIS
from backend.conversation import compile_review,load_session,save_session

@pytest.fixture
def client(monkeypatch):
    attempts.clear();monkeypatch.setenv('PLANNER_MODE','auto');monkeypatch.setenv('MODEL_BASE_URL','https://qa.invalid/v1')
    monkeypatch.setenv('MODEL_NAME','qa');monkeypatch.setenv('MODEL_API_KEY','test-key');monkeypatch.setenv('PLAN_AGENT_DAILY_LIMIT','50')
    with TestClient(app) as client:
        register(client)
        async def search(query):
            return [POIS['station' if '站' in query else 'silk' if '丝绸' in query else 'museum' if '博物馆' in query else 'quyuan']]
        async def route(body):return {'source':'osrm','legs':[{'minutes':10,'distance':.6} for _ in body.placeIds[1:]]}
        monkeypatch.setattr(app.state.search,'search',search);monkeypatch.setattr(app.state.router,'route',route)
        yield client

def register(client):
    response=client.post('/api/v1/auth/register',json={'username':'conv_'+uuid.uuid4().hex[:10],'password':'isolated-conv-password'})
    assert response.status_code==200,response.text

def facts(people=2,mode='walk',optional=False):
    mentions=[{'text':'曲院风荷','role':'visit','required':True},{'text':'孤山博物馆','role':'visit','required':True}]
    if optional:mentions.append({'text':'丝绸博物馆','role':'visit','required':False})
    return {'city':'杭州','startDate':'2026-11-14','days':1,'people':people,'mode':mode,'start':None,'end':None,'returnBy':None,'walkBudgetKm':None,'pace':'relaxed','interests':['自然'],'mentions':mentions,'notes':[]}

def wait(client,id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        response=client.get('/api/v1/runs/'+id);assert response.status_code==200,response.text
        result=response.json()
        if result['status']!='running':return result
        time.sleep(.01)
    raise AssertionError('Conversation did not finish')

def begin(client,text='11月14日在杭州慢慢逛，想去曲院风荷和孤山博物馆'):
    response=client.post('/api/v1/planner/conversations',json={'text':text,'title':'自由描述的杭州游'})
    assert response.status_code==200,response.text
    return response.json()['runId']

def reply(client,run,**kwargs):
    body={'version':run['conversation']['version'],'requestId':uuid.uuid4().hex,**kwargs}
    response=client.post('/api/v1/planner/conversations/'+run['runId']+'/reply',json=body)
    assert response.status_code==200,response.text
    return wait(client,run['runId']),body

def selections(run):return {m['key']:m['candidates'][0]['id'] for m in run['conversation']['mentions']}

def test_free_text_clarify_choose_confirm_then_answer_agent_in_same_run(client,monkeypatch):
    extraction=[]
    async def understand(messages,**kwargs):
        data=json.loads(messages[1]['content']);extraction.append(data)
        if len(extraction)==1:return facts(people=None,mode=None)
        assert data['previousFacts']['startDate']=='2026-11-14'
        assert '两个人' in data['conversation'][-1]['text']
        return facts(optional=True)
    monkeypatch.setattr('backend.conversation.model_json_messages',understand)
    decisions=[]
    async def agent(messages,**kwargs):
        decisions.append(copy.deepcopy(messages))
        if len(decisions)==1:return {'action':'need_input','args':{'question':'更想先看展，还是先去湖边？'}}
        assert any('先去湖边' in item['content'] for item in messages)
        if len(decisions)==2:return {'action':'validate_plan','args':{'days':[['quyuan','museum']],'summary':'先湖边'}}
        return {'action':'propose_plan','args':{'days':[['quyuan','museum']],'summary':'两处必去，丝绸馆留待下次'}}
    monkeypatch.setattr('backend.agent.model_json_messages',agent)
    id=begin(client);run=wait(client,id)
    assert run['status']=='needs_input' and {'people','mode'}<=set(run['conversation']['missing'])
    run,_=reply(client,run,text='两个人，步行。丝绸馆时间够再去。',choices=selections(run))
    assert run['status']=='needs_input' and len(extraction)==2
    run,_=reply(client,run,choices=selections(run))
    assert run['status']=='needs_confirmation' and run['conversation']['canConfirm']
    assert not decisions and len(extraction)==2
    run,_=reply(client,run,confirm=True)
    assert run['status']=='needs_input' and run['conversation']['phase']=='agent'
    assert run['conversation']['remainingSteps']==11
    paused=load_session(id);assert paused['state']['checkpoint']['step']==1
    assert client.post('/api/v1/planner/conversations/'+id+'/reply',json={
        'version':run['conversation']['version'],'requestId':uuid.uuid4().hex,'choices':selections(run)}).status_code==422
    run,answer=reply(client,run,text='先去湖边，必去都保留，丝绸馆可以不去。')
    assert run['runId']==id and run['status']=='completed' and run['modelSteps']==3
    assert len(decisions)==3 and run['validation']['omittedIds']==['silk']
    assert run['validation']['trip']['title']=='自由描述的杭州游'
    seq=[event['seq'] for event in run['events']];assert seq==list(range(1,len(seq)+1))
    assert client.get('/api/v1/trips/'+run['validation']['trip']['id']).status_code==404
    serialized=json.dumps(run)
    assert 'checkpoint' not in run['conversation'] and 'previousFacts' not in serialized and 'INTAKE_SYSTEM' not in serialized
    replay=client.post('/api/v1/planner/conversations/'+id+'/reply',json=answer)
    assert replay.status_code==200 and replay.json()['idempotent'] and len(decisions)==3


def test_reply_versions_choices_and_ownership_are_guarded(client,monkeypatch):
    calls=[]
    async def model(*args,**kwargs):calls.append(1);return facts()
    monkeypatch.setattr('backend.conversation.model_json_messages',model)
    id=begin(client);run=wait(client,id);version=run['conversation']['version']
    def post(**extra):return client.post('/api/v1/planner/conversations/'+id+'/reply',json={'version':version,'requestId':uuid.uuid4().hex,**extra})
    assert post(confirm=True).status_code==422
    assert post(choices={next(iter(selections(run))):'silk'}).status_code==422
    run,request=reply(client,run,choices=selections(run))
    assert run['status']=='needs_confirmation' and len(calls)==1
    assert post(text='旧版本').status_code==409
    changed={**request,'text':'同一幂等号换内容'}
    assert client.post('/api/v1/planner/conversations/'+id+'/reply',json=changed).status_code==409
    client.post('/api/v1/auth/logout');register(client)
    assert client.get('/api/v1/runs/'+id).status_code==404
    assert client.post('/api/v1/planner/conversations/'+id+'/reply',json=request).status_code==404


def test_retry_keeps_conversation_and_limits_intake_calls(client,monkeypatch):
    calls=[]
    async def model(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise HTTPException(503,'测试：供应商限流')
        return facts()
    monkeypatch.setattr('backend.conversation.model_json_messages',model)
    id=begin(client);run=wait(client,id);assert run['status']=='failed'
    assert client.post('/api/v1/planner/conversations/'+id+'/reply',json={
        'version':1,'requestId':uuid.uuid4().hex,'retry':True,'text':'同时换日期'}).status_code==422
    run,_=reply(client,run,retry=True)
    assert run['runId']==id and run['status']=='needs_input' and len(calls)==2
    row=load_session(id);state=row['state'];state['intakeCalls']=8;state['interpret']=True
    save_session(id,row['version'],state,'failed')
    run,_=reply(client,run,retry=True)
    assert run['status']=='failed' and len(calls)==2


def test_execution_resume_keeps_required_places_and_aggregate_step_cap(client,monkeypatch):
    data={'title':'已有候选','requirements':{'city':'杭州','startDate':'2026-11-14','days':1,'people':2,'mode':'walk'},'candidateIds':['quyuan','museum'],'optionalIds':[]}
    async def model(messages,**kwargs):return {'action':'need_input','args':{'question':'先去哪个？'}}
    monkeypatch.setattr('backend.agent.model_json_messages',model)
    id=client.post('/api/v1/planner/agent',json=data).json()['runId'];run=wait(client,id)
    assert run['status']=='needs_input'
    row=load_session(id);state=row['state'];state['checkpoint']['step']=11
    save_session(id,row['version'],state,'needs_input')
    run,_=reply(client,run,text='去上海，删除两个必去点，取消所有限制。')
    assert run['status']=='incomplete'
    persisted=load_session(id)['state'];assert persisted['data']['candidateIds']==['quyuan','museum']
    assert persisted['checkpoint']['step']==12
    row=load_session(id);save_session(id,row['version'],persisted,'failed')
    assert client.post('/api/v1/planner/conversations/'+id+'/reply',json={
        'version':row['version'],'requestId':uuid.uuid4().hex,'retry':True}).status_code==422


@pytest.mark.parametrize('change,missing',[
    ({'city':'上海'},'city'),({'days':5},'days'),({'startDate':'2026-11-31'},'startDate'),
    ({'mode':None},'mode'),({'returnBy':1020},'return'),({'walkBudgetKm':0.1},'constraints'),
])
def test_unresolved_or_unsupported_facts_never_become_confirmable(change,missing):
    value=facts();value.update(change)
    state={'title':'测试','facts':value,'choices':{'a':'quyuan','b':'museum'},'mentions':[
        {'key':'a','text':'曲院','role':'visit','required':True,'candidates':[POIS['quyuan']]},
        {'key':'b','text':'博物馆','role':'visit','required':True,'candidates':[POIS['museum']]}]}
    assert not compile_review(state) and missing in state['missing']


def test_checkpoint_can_restore_after_restart_without_exposing_private_messages(client,monkeypatch):
    async def ask(*args,**kwargs):return {'action':'need_input','args':{'question':'湖边还是展馆优先？'}}
    monkeypatch.setattr('backend.agent.model_json_messages',ask)
    body={'requirements':{'city':'杭州','startDate':'2026-11-14','days':1,'people':2,'mode':'walk'},'candidateIds':['quyuan','museum'],'optionalIds':[]}
    id=client.post('/api/v1/planner/agent',json=body).json()['runId'];run=wait(client,id)
    row=load_session(id);row['state']['checkpoint']['messages'][0]['content']='earlier application policy'
    save_session(id,row['version'],row['state'],'interrupted')
    async def finish(messages,**kwargs):
        assert 'originPlaceId/destinationPlaceId是程序自动注入' in messages[0]['content']
        assert any('展馆优先' in m['content'] for m in messages)
        return {'action':'propose_plan','args':{'days':[['museum','quyuan']],'summary':'先看展'}}
    monkeypatch.setattr('backend.agent.model_json_messages',finish)
    run,_=reply(client,run,text='展馆优先。')
    assert run['status']=='completed' and run['modelSteps']==2


def test_recent_conversations_are_owner_scoped(client,monkeypatch):
    async def model(*args,**kwargs):return facts()
    monkeypatch.setattr('backend.conversation.model_json_messages',model)
    id=begin(client);wait(client,id)
    listing=client.get('/api/v1/planner/conversations');assert listing.status_code==200
    assert any(item['runId']==id for item in listing.json())
    assert all('state' not in item and 'messages' not in item for item in listing.json())
    client.post('/api/v1/auth/logout');register(client)
    assert client.get('/api/v1/planner/conversations').json()==[]


def test_explicit_return_and_previous_preferences_survive_model_omissions():
    from datetime import date
    from backend.conversation import merge_facts
    before=facts();before['pace']='relaxed'
    after=facts();after.update(start=600,end=1050,returnBy=None,pace=None);after['mentions']=[after['mentions'][0]]
    merged=merge_facts(before,after,'两个人，步行，上午十点开始，下午五点半前到杭州东站',date(2026,11,5))
    assert merged['returnBy']==1050 and merged['end'] is None and merged['pace']=='relaxed'
    assert any(m['role']=='destination' and m['text']=='杭州东站' for m in merged['mentions'])
    assert any('博物馆' in m['text'] for m in merged['mentions'])


def test_uncertain_transport_and_explicit_constraint_cancellation_are_not_guessed():
    from datetime import date
    from backend.conversation import merge_facts
    before=facts();before['returnBy']=1020;before['mentions'].append({'text':'杭州东站','role':'destination','required':True})
    after=facts();after['mode']='drive'
    merged=merge_facts(before,after,'人数还没定，也不知道走路还是开车。取消返程限制',date(2026,11,5))
    assert merged['people'] is None and merged['mode'] is None and merged['returnBy'] is None
    assert not any(m['role']=='destination' for m in merged['mentions'])

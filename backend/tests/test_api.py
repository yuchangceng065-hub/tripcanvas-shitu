import copy, os, tempfile, uuid
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
from fastapi.testclient import TestClient
from backend.app import app,Trip,verify_proposal,issues,attempts
import pytest

@pytest.fixture
def client():
    attempts.clear()
    with TestClient(app) as c:yield c

def register(c,name=None):
    r=c.post('/api/v1/auth/register',json={'username':name or 'user_'+uuid.uuid4().hex[:8],'password':'correct-horse-824!'});assert r.status_code==200,r.text

def trip():
    return {'id':str(uuid.uuid4()),'title':'测试旅行','city':'杭州','people':2,'mode':'walk','revision':1,'days':[{'date':'2026-11-07','start':570,'end':1110,'activities':[{'id':'a','placeId':'quyuan','duration':90,'lock':'keep'},{'id':'b','placeId':'louwailou','duration':75,'lock':'fixed','fixedStart':780}]}]}

def test_auth_ownership_and_revocable_snapshot(client):
    register(client);t=trip();assert client.put('/api/v1/trips/'+t['id'],json={'trip':t}).status_code==200
    shared=client.post('/api/v1/trips/'+t['id']+'/shares').json()
    assert client.get('/api/v1/public/shares/'+shared['token']).json()['title']=='测试旅行'
    t['revision']=2;t['title']='新的版本';assert client.put('/api/v1/trips/'+t['id'],json={'trip':t}).status_code==200
    assert client.get('/api/v1/public/shares/'+shared['token']).json()['title']=='测试旅行'
    client.post('/api/v1/auth/logout');register(client)
    assert client.get('/api/v1/trips/'+t['id']).status_code==404
    assert client.delete('/api/v1/shares/'+shared['id']).status_code==404

def test_share_revoke(client):
    register(client);t=trip();client.put('/api/v1/trips/'+t['id'],json={'trip':t})
    share=client.post('/api/v1/trips/'+t['id']+'/shares').json()
    assert client.delete('/api/v1/shares/'+share['id']).status_code==200
    assert client.get('/api/v1/public/shares/'+share['token']).status_code==404

def test_immutable_revision_idempotent_sync(client):
    register(client);t=trip();url='/api/v1/trips/'+t['id']
    assert client.put(url,json={'trip':t}).status_code==200
    assert client.put(url,json={'trip':t}).status_code==200
    assert len(client.get(url+'/versions').json())==1
    t['title']='未经版本更新的覆盖';assert client.put(url,json={'trip':t}).status_code==409

def test_lock_validation():
    before=Trip.model_validate(trip());after=before.model_copy(deep=True);after.days[0].activities.pop(0)
    with pytest.raises(ValueError,match='锁定'):verify_proposal(before,after,0)
    after=before.model_copy(deep=True);after.days[0].activities[1].fixedStart+=30
    with pytest.raises(ValueError,match='固定'):verify_proposal(before,after,0)

def test_conflict_and_invalid_place():
    data=trip();data['days'][0]['start']=800;assert issues(Trip.model_validate(data))
    data['days'][0]['activities'][0]['placeId']='invented'
    with pytest.raises(ValueError):Trip.model_validate(data)

def test_csrf_and_no_model(client):
    assert client.post('/api/v1/auth/login',headers={'origin':'https://untrusted.invalid'},json={'username':'abc','password':'12345678'}).status_code==403
    register(client);assert client.post('/api/v1/planner/suggest',json={'trip':trip(),'day':0,'prompt':'轻松一点'}).status_code==503

def test_bearer_session_survives_without_third_party_cookies_and_revokes(client):
    response=client.post('/api/v1/auth/register',json={'username':'bearer_'+uuid.uuid4().hex[:8],'password':'test-password-123'})
    assert response.headers['cache-control']=='no-store'
    token=response.json()['access_token'];client.cookies.clear()
    assert client.get('/api/v1/auth/me').status_code==401
    headers={'Authorization':'Bearer '+token}
    assert client.get('/api/v1/auth/me',headers=headers).status_code==200
    assert client.post('/api/v1/auth/logout',headers=headers).status_code==200
    assert client.get('/api/v1/auth/me',headers=headers).status_code==401

def test_route_schema_and_cors(client):
    assert client.post('/api/v1/routes',json={'mode':'walk','placeIds':['invented','quyuan']}).status_code==422
    assert client.post('/api/v1/routes',json={'mode':'flight','placeIds':['museum','quyuan']}).status_code==422
    response=client.options('/api/v1/routes',headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Authorization,Content-Type'})
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']=='http://127.0.0.1:5173'

def test_render_rejects_ephemeral_sqlite():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c','import backend.app'],env={**os.environ,'RENDER':'true','DATABASE_URL':'sqlite:///:memory:'},capture_output=True,text=True)
    assert result.returncode!=0 and 'refusing ephemeral SQLite' in result.stderr

def draft_body():
    return {'title':'确认候选的新旅行','requirements':{'city':'杭州','startDate':'2026-11-14','days':1,'people':2,'mode':'walk','start':570,'end':1110,'pace':'balanced','interests':['博物馆']},'candidateIds':['museum','quyuan'],'useModel':False}

def test_draft_preview_does_not_save_and_refuses_invented_places(client,monkeypatch):
    register(client)
    async def roads(body):return {'source':'osrm','legs':[{'minutes':10,'distance':.6} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router,'route',roads)
    result=client.post('/api/v1/planner/draft',json=draft_body())
    assert result.status_code==200,result.text
    data=result.json();assert data['source']=='heuristic' and not data['issues']
    assert set(a['placeId'] for d in data['trip']['days'] for a in d['activities'])=={'museum','quyuan'}
    assert client.get('/api/v1/trips/'+data['trip']['id']).status_code==404
    assert client.put('/api/v1/trips/'+data['trip']['id'],json={'trip':data['trip']}).status_code==200
    bad=draft_body();bad['candidateIds']=['invented'];assert client.post('/api/v1/planner/draft',json=bad).status_code==422

def test_dynamic_place_is_authoritative_persistent_and_shared(client,monkeypatch):
    from backend.catalog import normalize
    from backend.app import POIS,PlaceRecord,SessionLocal
    register(client)
    place=normalize({'properties':{'name':'测试真实地点','osm_type':'N','osm_id':987654321,'city':'杭州市','street':'测试路'},'geometry':{'type':'Point','coordinates':[120.14,30.25]}})
    async def search(q):return [place]
    monkeypatch.setattr(app.state.search,'search',search)
    result=client.get('/api/v1/places/search?q=真实地点');assert result.status_code==200
    assert result.json()['places'][0]['address']=='杭州市 · 测试路'
    with SessionLocal() as db:assert db.get(PlaceRecord,place['id']) is not None
    t=trip();t['days'][0]['activities'][0]['placeId']=place['id'];t['placeSnapshots']=[{**place,'lng':1}]
    assert client.put('/api/v1/trips/'+t['id'],json={'trip':t}).status_code==200
    saved=client.get('/api/v1/trips/'+t['id']).json();assert saved['placeSnapshots'][0]['lng']==120.14
    share=client.post('/api/v1/trips/'+t['id']+'/shares').json()
    assert client.get('/api/v1/public/shares/'+share['token']).json()['placeSnapshots'][0]['id']==place['id']

def test_model_cannot_invent_candidates(client,monkeypatch):
    import backend.app as module
    register(client)
    async def hallucinate(system,data):return {'days':[['invented']]}
    monkeypatch.setattr(module,'model_json',hallucinate)
    body=draft_body();body['useModel']=True
    assert client.post('/api/v1/planner/draft',json=body).status_code==422

def test_return_deadline_conflict_and_requirements_lock(client,monkeypatch):
    register(client)
    async def slow(body):return {'source':'osrm','legs':[{'minutes':500,'distance':30} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router,'route',slow)
    body=draft_body();body['requirements'].update(destinationPlaceId='station',returnBy=1020)
    data=client.post('/api/v1/planner/draft',json=body).json();assert data['issues']
    before=Trip.model_validate(data['trip']);after=before.model_copy(deep=True);after.requirements.pace='full'
    with pytest.raises(ValueError,match='需求'):verify_proposal(before,after,0)

def test_model_edits_only_selected_day_and_preserves_saved_trip(client,monkeypatch):
    import backend.app as module
    register(client);t=trip()
    t['days'].append({**copy.deepcopy(t['days'][0]),'date':'2026-11-08','activities':[]})
    client.put('/api/v1/trips/'+t['id'],json={'trip':t})
    for key in ('MODEL_API_KEY','MODEL_BASE_URL','MODEL_NAME'):monkeypatch.setenv(key,'test')
    async def edit(system,data,**kwargs):return {'summary':'晚一点出发','changes':['出发推迟15分钟'],'start':585,'activities':data['currentDay']['activities']}
    async def roads(body):return {'source':'osrm','legs':[{'minutes':10,'distance':1} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(module,'model_json',edit);monkeypatch.setattr(app.state.router,'route',roads)
    r=client.post('/api/v1/planner/suggest',json={'trip':t,'day':0,'prompt':'晚一点出发'})
    assert r.status_code==200,r.text
    candidate=r.json()['trip'];assert candidate['days'][0]['start']==585 and candidate['days'][1]==t['days'][1]
    assert candidate['title']==t['title'] and candidate['revision']==1
    assert client.get('/api/v1/trips/'+t['id']).json()['days'][0]['start']==570
    async def remove_locked(system,data,**kwargs):return {'summary':'错误删除','changes':[],'start':585,'activities':[]}
    monkeypatch.setattr(module,'model_json',remove_locked)
    assert client.post('/api/v1/planner/suggest',json={'trip':t,'day':0,'prompt':'保留锁定'}).status_code==422

def test_late_fixed_activity_does_not_move_following_arrivals_backwards():
    t=trip();t['days'][0].update(start=800,end=1000)
    t['days'][0]['activities'].append({'id':'c','placeId':'museum','duration':60,'lock':'none'})
    result=issues(Trip.model_validate(t),{0:{'legs':[{'minutes':10},{'minutes':10}]}})
    assert {i['id'] for i in result}=={'b','c'}

def test_model_parser_accepts_only_complete_json_objects():
    from backend.app import parse_model_json
    assert parse_model_json('```json\n{"summary":"室内游览"}\n```')=={'summary':'室内游览'}
    for content,reason in [('',None),('{"summary":', 'length'),('建议如下：{"summary":"a"}',None),('[]',None)]:
        with pytest.raises(ValueError):parse_model_json(content,reason)

def test_free_glm_disables_thinking_and_repairs_locked_edit_once(client,monkeypatch):
    import backend.app as module
    import httpx,json
    register(client);t=trip();t['days'][0]['activities'].append({'id':'c','placeId':'museum','duration':60,'lock':'none'})
    for key,value in {'MODEL_API_KEY':'fake-key','MODEL_BASE_URL':'https://aihubmix.com/v1','MODEL_NAME':'coding-glm-5.1-free'}.items():monkeypatch.setenv(key,value)
    good={'summary':'减少一站','changes':['移除未锁定地点'],'start':570,'activities':t['days'][0]['activities'][:2]}
    bad=copy.deepcopy(good);bad['activities'][1]['duration']+=15
    outputs=[bad,good];requests=[]
    class Provider:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            text='```json\n'+json.dumps(outputs.pop(0))+'\n```'
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':text}}]},request=httpx.Request('POST',url))
    async def roads(body):return {'source':'osrm','legs':[{'minutes':10,'distance':1} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(module.httpx,'AsyncClient',Provider);monkeypatch.setattr(app.state.router,'route',roads)
    r=client.post('/api/v1/planner/suggest',json={'trip':t,'day':0,'prompt':'少去一站'})
    assert r.status_code==200,r.text
    assert len(requests)==2
    assert requests[0]['thinking']=={'type':'disabled'}
    assert r.json()['source']=='model'
    assert r.json()['trip']['days'][0]['activities'][1]==t['days'][0]['activities'][1]
    assert '固定安排不能修改' in requests[1]['messages'][-1]['content']

def test_rules_mode_health_and_no_model_calls(client,monkeypatch):
    import asyncio,backend.app as module
    for key in ('MODEL_API_KEY','MODEL_BASE_URL','MODEL_NAME'):monkeypatch.setenv(key,'configured')
    monkeypatch.setenv('PLANNER_MODE','rules')
    data=client.get('/api/v1/health').json();assert data['model'] is False and data['plannerMode']=='rules'
    with pytest.raises(module.HTTPException) as caught:asyncio.run(module.model_json('system',{}))
    assert caught.value.status_code==503

def test_provider_trial_notice_is_not_retried_as_invalid_json():
    from backend.app import parse_model_json,HTTPException
    with pytest.raises(HTTPException) as caught:
        parse_model_json('Sorry, to prevent abuse of free resources, accounts that have not been recharged can only try 10 times. You can increase the free quota after recharging; https://console.aihubmix.com/topup')
    assert caught.value.status_code==503
    assert '额度已用尽' in caught.value.detail

def test_amap_request_budgets_are_persistent_and_atomic(client,monkeypatch):
    from backend.app import reserve_amap_usage,ProviderUsage,SessionLocal,HTTPException
    from sqlalchemy import select
    monkeypatch.setenv('AMAP_LBS_MONTHLY_LIMIT','2');monkeypatch.setenv('AMAP_LBS_DAILY_LIMIT','1')
    reserve_amap_usage('lbs')
    with pytest.raises(HTTPException) as caught:reserve_amap_usage('lbs')
    assert caught.value.status_code==429
    with SessionLocal() as db:
        counts=[row.count for row in db.scalars(select(ProviderUsage).where(ProviderUsage.key.like('amap:lbs:%')))]
    assert counts==[1,1]  # monthly reservation rolls back when the daily budget fails
    with pytest.raises(HTTPException):reserve_amap_usage('lbs')

def test_map_config_never_exposes_private_keys_and_proxy_rejects_untrusted_requests(client,monkeypatch):
    monkeypatch.setenv('AMAP_WEB_KEY','private-web-secret');monkeypatch.setenv('AMAP_JS_KEY','domain-bound-public-js');monkeypatch.setenv('AMAP_JS_SECURITY_CODE','private-security-code')
    data=client.get('/api/v1/map/config');assert data.status_code==200
    assert data.json()['jsKey']=='domain-bound-public-js'
    assert 'private-web-secret' not in data.text and 'private-security-code' not in data.text
    assert client.get('/_AMapService/v4/map/styles?key=domain-bound-public-js',headers={'Origin':'https://untrusted.invalid'}).status_code==403
    assert client.get('/_AMapService/v5/direction/driving?key=domain-bound-public-js',headers={'Origin':'http://127.0.0.1:5173'}).status_code==404

def test_anonymous_daily_budget_is_bounded(client):
    from backend.app import reserve_anonymous,HTTPException
    for _ in range(3):reserve_anonymous('unit-test',3)
    with pytest.raises(HTTPException) as caught:reserve_anonymous('unit-test',3)
    assert caught.value.status_code==429
    assert '未登录' in caught.value.detail

def test_same_host_origin_passes_behind_tls_proxy(client):
    response=client.post('/api/v1/routes',json={'mode':'walk','placeIds':['invented','quyuan']},
        headers={'Origin':'https://tripcanvas-api.onrender.com','Host':'tripcanvas-api.onrender.com'})
    # Origin accepted, unknown place rejected by the route contract.
    assert response.status_code==422
    rejected=client.post('/api/v1/routes',json={'mode':'walk','placeIds':['invented','quyuan']},
        headers={'Origin':'https://evil.example','Host':'tripcanvas-api.onrender.com'})
    assert rejected.status_code==403

def test_signed_in_route_calls_use_the_account_bucket(client):
    register(client)
    me=client.get('/api/v1/auth/me').json()
    login=client.post('/api/v1/auth/login',json={'username':me['username'],'password':'correct-horse-824!'}).json()
    response=client.post('/api/v1/routes',json={'mode':'walk','placeIds':['invented','quyuan']},
        headers={'Authorization':'Bearer '+login['access_token']})
    assert response.status_code==422

def fast_roads(body):
    return {'source':'osrm','legs':[{'minutes':10,'distance':.6} for _ in body.placeIds[1:]]}

def test_confirm_is_authoritative_and_idempotent(client,monkeypatch):
    register(client)
    async def roads(body):return fast_roads(body)
    monkeypatch.setattr(app.state.router,'route',roads)
    t=trip()
    first=client.post('/api/v1/planner/confirm',json={'trip':t,'baseRevision':0})
    assert first.status_code==200,first.text
    assert first.json()['revision']==1 and first.json()['idempotent'] is False
    assert client.get('/api/v1/trips/'+t['id']).json()['revision']==1
    replay=client.post('/api/v1/planner/confirm',json={'trip':t,'baseRevision':0})
    assert replay.status_code==200 and replay.json()['idempotent'] is True
    stale=copy.deepcopy(t);stale['revision']=2
    assert client.post('/api/v1/planner/confirm',json={'trip':stale,'baseRevision':0}).status_code==409

def test_confirm_rejects_infeasible_draft_with_repair_options(client,monkeypatch):
    register(client)
    async def slow(body):return {'source':'osrm','legs':[{'minutes':500,'distance':30} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router,'route',slow)
    t=trip()
    result=client.post('/api/v1/planner/confirm',json={'trip':t,'baseRevision':0})
    assert result.status_code==422
    detail=result.json()['detail']
    assert detail['code']=='infeasible'
    codes={issue['code'] for issue in detail['issues']}
    assert 'fixed_too_early' in codes or 'day_overflow' in codes
    assert any(issue['options'] for issue in detail['issues'])
    assert client.get('/api/v1/trips/'+t['id']).status_code==404

def test_confirm_refuses_locked_changes_and_keeps_base_version(client,monkeypatch):
    register(client)
    async def roads(body):return fast_roads(body)
    monkeypatch.setattr(app.state.router,'route',roads)
    t=trip()
    assert client.post('/api/v1/planner/confirm',json={'trip':t,'baseRevision':0}).status_code==200
    changed=copy.deepcopy(t);changed['revision']=2;changed['days'][0]['activities'][0]['placeId']='museum'
    result=client.post('/api/v1/planner/confirm',json={'trip':changed,'baseRevision':1})
    assert result.status_code==422 and result.json()['detail']['code']=='locked_violation'
    assert client.get('/api/v1/trips/'+t['id']).json()['revision']==1

def test_requirements_returns_real_candidates_for_each_mention(client,monkeypatch):
    register(client)
    calls=[]
    async def fake(q):
        calls.append(q)
        return [{'id':'osm:node:'+str(len(calls)),'name':q+' 候选','address':'杭州'}]
    monkeypatch.setattr(app.state.search,'search',fake)
    text='2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。'
    response=client.post('/api/v1/planner/requirements',json={'text':text})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['requirements']['returnBy']==1020
    assert [mention['text'] for mention in data['mentions']]==['西湖','浙江工业大学','杭州东站']
    assert all(mention['candidates'] for mention in data['mentions'])
    assert data['suggestions'][0]['name'].startswith('咖啡')
    assert '浙江工业大学' in calls

def test_requirements_requires_login(client):
    assert client.post('/api/v1/planner/requirements',json={'text':'去杭州两天'}).status_code==401

def test_one_sentence_to_confirmed_trip_closed_loop(client,monkeypatch):
    register(client)
    async def search(q):
        return [{'id':'amap:'+q,'name':q,'kind':'景点','tag':'搜索确认地点','description':'测试地点','duration':60,'cost':0,
                 'lng':120.15,'lat':30.26,'x':0,'y':0,'indoor':False,'image':'lake','crs':'GCJ02','provider':'amap'}]
    monkeypatch.setattr(app.state.search,'search',search)
    async def roads(body):return fast_roads(body)
    monkeypatch.setattr(app.state.router,'route',roads)
    text='2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。'
    parsed=client.post('/api/v1/planner/requirements',json={'text':text}).json()
    mentions={mention['text']:mention for mention in parsed['mentions']}
    visits=[mentions['西湖']['candidates'][0]['id'],mentions['浙江工业大学']['candidates'][0]['id']]
    destination=mentions['杭州东站']['candidates'][0]['id']
    requirements={**parsed['requirements'],'destinationPlaceId':destination}
    draft=client.post('/api/v1/planner/draft',json={'title':'样例两日','requirements':requirements,'candidateIds':visits,'useModel':False})
    assert draft.status_code==200,draft.text
    data=draft.json();assert data['issues']==[]
    trip=data['trip']
    confirmed=client.post('/api/v1/planner/confirm',json={'trip':trip,'baseRevision':0})
    assert confirmed.status_code==200,confirmed.text
    assert confirmed.json()['revision']==1
    saved=client.get('/api/v1/trips/'+trip['id']).json()
    placed=[activity['placeId'] for day in saved['days'] for activity in day['activities']]
    assert destination in placed and all(place in placed for place in visits)
    station=[activity for day in saved['days'] for activity in day['activities'] if activity['placeId']==destination][0]
    assert station['lock']=='fixed' and station['fixedStart']==17*60

def test_walk_budget_is_a_hard_constraint_with_drop_options(client,monkeypatch):
    register(client)
    async def walked(body):
        return {'source':'amap','crs':'GCJ02','legs':[{'distance':2.0,'minutes':30,
            'segments':[{'mode':'walk','distance':2.0,'minutes':30}]} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router,'route',walked)
    body=draft_body();body['requirements']['walkBudgetKm']=0.5
    data=client.post('/api/v1/planner/draft',json=body).json()
    budget=[issue for issue in data['issues'] if issue['code']=='walk_budget']
    assert budget and budget[0]['severity']=='error'
    assert any(option['action']=='drop' for option in budget[0]['options'])
    confirmed=client.post('/api/v1/planner/confirm',json={'trip':data['trip'],'baseRevision':0})
    assert confirmed.status_code==422 and confirmed.json()['detail']['code']=='infeasible'

def test_lunch_gap_warns_but_does_not_block_confirmation(client,monkeypatch):
    register(client)
    async def roads(body):return {'source':'osrm','legs':[{'minutes':10,'distance':.6} for _ in body.placeIds[1:]]}
    monkeypatch.setattr(app.state.router,'route',roads)
    body=draft_body();body['candidateIds']=['museum','quyuan','baidi']
    data=client.post('/api/v1/planner/draft',json=body).json()
    meals=[issue for issue in data['issues'] if issue['code']=='meal_break']
    assert meals and meals[0]['severity']=='warning'
    confirmed=client.post('/api/v1/planner/confirm',json={'trip':data['trip'],'baseRevision':0})
    assert confirmed.status_code==200,confirmed.text

def test_model_options_disable_hidden_thinking_for_glm_providers():
    from backend.app import model_options
    zai=model_options('https://api.z.ai/api/paas/v4','glm-5.1')
    assert zai['thinking']=={'type':'disabled'}
    assert model_options('https://api.z.ai/api/paas/v4','glm-4.7-flash')['thinking']=={'type':'disabled'}
    assert 'thinking' not in model_options('https://api.z.ai/api/paas/v4','qwen3')
    hub=model_options('https://aihubmix.com/v1','coding-glm-5.1-free')
    assert hub['reasoning_effort']=='none' and hub['thinking']=={'type':'disabled'}

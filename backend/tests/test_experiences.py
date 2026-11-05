import copy,uuid
import pytest
from fastapi.testclient import TestClient
from backend.app import app,attempts
from backend.places import POIS
from backend.experiences import ground_guide

@pytest.fixture
def client(monkeypatch):
    attempts.clear()
    with TestClient(app) as c:
        c.post('/api/v1/auth/register',json={'username':'exp_'+uuid.uuid4().hex[:8],'password':'test-experience-only'})
        async def road(body):return {'source':'osrm','mode':body.mode,'placeIds':body.placeIds,'legs':[{'minutes':20,'distance':1} for _ in body.placeIds[1:]]}
        async def search(query):return [POIS['museum' if '博物馆' in query else 'quyuan']]
        monkeypatch.setattr(app.state.router,'route',road);monkeypatch.setattr(app.state.search,'search',search)
        yield c

def trip():
    return {'id':str(uuid.uuid4()),'title':'推演测试','city':'杭州','people':2,'mode':'walk','revision':1,
        'requirements':{'city':'杭州','startDate':'2026-11-14','days':1,'people':2,'mode':'walk','start':570,'end':1110,'mustVisitIds':['quyuan'],'destinationPlaceId':'station','returnBy':1020},
        'days':[{'date':'2026-11-14','start':570,'end':1110,'activities':[{'id':'q','placeId':'quyuan','duration':90,'lock':'keep'},{'id':'m','placeId':'museum','duration':90,'lock':'none'},{'id':'s','placeId':'station','duration':15,'lock':'fixed','fixedStart':1020}]}]}

def source(c):
    t=trip();r=c.put('/api/v1/trips/'+t['id'],json={'trip':t});assert r.status_code==200;return t

def test_simulation_explains_repairs_and_respects_pins_without_writing(client):
    t=source(client);r=client.post('/api/v1/planner/simulate',json={'trip':t,'day':0,'start':900,'keepDeparture':True})
    assert r.status_code==200,r.text
    data=r.json();assert not data['baseline']['feasible'] and data['search']['evaluated']<=8
    feasible=[x for x in data['repairs'] if x['feasible']];assert feasible
    for item in feasible:
        assert item['trip']['days'][0]['start']==900
        assert item['trip']['days'][0]['activities'][-1]['fixedStart']==1020
        assert 'quyuan' in [a['placeId'] for a in item['trip']['days'][0]['activities']]
        assert item['changes'] and item['timeline']
    assert client.get('/api/v1/trips/'+t['id']).json()['days'][0]['start']==570
    run=client.get('/api/v1/runs/'+data['runId']).json();assert run['status']=='completed' and all('createdAt' in e for e in run['events'])
    locked=client.post('/api/v1/planner/simulate',json={'trip':t,'day':0,'start':900,'keepDurations':True,'keepPlaces':True}).json()
    assert not any(x['feasible'] for x in locked['repairs'])


def test_branches_persist_idempotent_open_independently_and_reject_stale_or_tampered(client):
    t=source(client);body={'sourceTripId':t['id'],'sourceRevision':1,'trip':t,'label':'早出发','requestId':uuid.uuid4().hex}
    r=client.post('/api/v1/branches',json=body);assert r.status_code==200,r.text
    branch=r.json();assert branch['trip']['id']!=t['id']
    assert client.post('/api/v1/branches',json=body).json()['idempotent']
    altered=copy.deepcopy(body);altered['label']='另一份';assert client.post('/api/v1/branches',json=altered).status_code==409
    altered=copy.deepcopy(body);altered['requestId']=uuid.uuid4().hex;altered['trip']['days'][0]['activities'][-1]['fixedStart']=1080
    assert client.post('/api/v1/branches',json=altered).status_code==422
    assert len(client.get('/api/v1/branches?sourceTripId='+t['id']).json())==1
    assert client.get('/api/v1/trips/'+branch['id']).status_code==404
    assert client.post('/api/v1/branches/'+branch['id']+'/preview').json()['feasible']
    assert client.post('/api/v1/branches/'+branch['id']+'/open').status_code==200
    assert len(client.get('/api/v1/trips/'+branch['id']+'/versions').json())==1
    assert client.post('/api/v1/branches/'+branch['id']+'/open').status_code==200
    t['revision']=2;client.put('/api/v1/trips/'+t['id'],json={'trip':t});body['requestId']=uuid.uuid4().hex
    assert client.post('/api/v1/branches',json=body).status_code==409
    client.post('/api/v1/auth/logout');client.post('/api/v1/auth/register',json={'username':'other_'+uuid.uuid4().hex[:8],'password':'test-experience-only'})
    assert client.get('/api/v1/branches?sourceTripId='+t['id']).status_code==404
    for suffix in ('/open','/preview'):assert client.post('/api/v1/branches/'+branch['id']+suffix).status_code==404
    assert client.delete('/api/v1/branches/'+branch['id']).status_code==404


def test_import_grounding_negation_dedup_and_owner_history(client,monkeypatch):
    text='曲院风荷适合散步；浙江省博物馆有空再去；不推荐去雷峰塔。'
    async def model(*args,**kwargs):return {'mentions':[{'name':'曲院风荷','quote':'曲院风荷适合散步','intent':'required'},{'name':'浙江省博物馆','quote':'浙江省博物馆有空再去','intent':'optional'},{'name':'雷峰塔','quote':'不推荐去雷峰塔','intent':'excluded'}]}
    monkeypatch.setattr('backend.routers.experiences.model_json_messages',model)
    r=client.post('/api/v1/planner/import-guide',json={'text':text});assert r.status_code==200,r.text
    data=r.json();assert len(data['mentions'])==3
    for m in data['mentions']:assert text[m['start']:m['end']]==m['quote']
    assert data['mentions'][-1]['candidates']==[]
    assert client.get('/api/v1/planner/imports').json()[0]['runId']==data['runId']
    assert client.get('/api/v1/runs').json()[0]['kind']=='guide_import'
    client.post('/api/v1/auth/logout');client.post('/api/v1/auth/register',json={'username':'other_'+uuid.uuid4().hex[:8],'password':'test-experience-only'})
    assert client.get('/api/v1/planner/imports').json()==[]
    assert client.get('/api/v1/runs/'+data['runId']).status_code==404
    with pytest.raises(ValueError):ground_guide(text,{'mentions':[{'name':'曲院风荷','quote':'我编造的曲院风荷出处','intent':'required'}]})

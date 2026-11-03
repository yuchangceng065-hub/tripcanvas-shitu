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

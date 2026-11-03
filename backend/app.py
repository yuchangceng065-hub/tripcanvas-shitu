"""TripCanvas API. SQLite for local development; PostgreSQL via DATABASE_URL."""
from __future__ import annotations
import asyncio, hashlib, hmac, json, math, os, re, secrets, time, uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import create_engine, String, Text, Integer, Float, select, delete, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from backend.routing import Router, RouteBody

ROOT=Path(__file__).parent
PLACES=json.loads((ROOT/'places.json').read_text())
POIS={p['id']:p for p in PLACES}
DATABASE_URL=os.getenv('DATABASE_URL',f'sqlite:///{ROOT / "tripcanvas.db"}')
if DATABASE_URL.startswith(('postgres://','postgresql://')):
    DATABASE_URL='postgresql+psycopg://'+DATABASE_URL.split('://',1)[1]
if os.getenv('RAILWAY_ENVIRONMENT_ID') and not os.getenv('DATABASE_URL'):
    raise RuntimeError('Railway requires a persistent DATABASE_URL; refusing ephemeral SQLite')
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args={'check_same_thread':False} if DATABASE_URL.startswith('sqlite') else {})
SessionLocal=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    username:Mapped[str]=mapped_column(String,unique=True)
    password:Mapped[str]=mapped_column(String)
class AuthSession(Base):
    __tablename__='sessions'
    token:Mapped[str]=mapped_column(String,primary_key=True)
    owner:Mapped[str]=mapped_column(String,index=True)
    expires:Mapped[float]=mapped_column(Float)
class TripRow(Base):
    __tablename__='trips'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    owner:Mapped[str]=mapped_column(String,index=True)
    revision:Mapped[int]=mapped_column(Integer)
    snapshot:Mapped[str]=mapped_column(Text)
class VersionRow(Base):
    __tablename__='versions'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    trip_id:Mapped[str]=mapped_column(String,index=True)
    revision:Mapped[int]=mapped_column(Integer)
    snapshot:Mapped[str]=mapped_column(Text)
    created:Mapped[float]=mapped_column(Float)
class Share(Base):
    __tablename__='shares'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    token:Mapped[str]=mapped_column(String,unique=True,index=True)
    owner:Mapped[str]=mapped_column(String,index=True)
    trip_id:Mapped[str]=mapped_column(String,index=True)
    snapshot:Mapped[str]=mapped_column(Text)
    expires:Mapped[float]=mapped_column(Float)
class Run(Base):
    __tablename__='runs'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    owner:Mapped[str]=mapped_column(String,index=True)
    trip_id:Mapped[str]=mapped_column(String,index=True)
    base_revision:Mapped[int]=mapped_column(Integer)
    result:Mapped[str]=mapped_column(Text)
    created:Mapped[float]=mapped_column(Float)
    applied:Mapped[int]=mapped_column(Integer,default=0)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    async with httpx.AsyncClient(timeout=20,headers={'User-Agent':'TripCanvas/1.1 (+https://tripcanvas-shitu-melody.yuchangceng065.chatgpt.site)'}) as client:
        app.state.router=Router(client,POIS)
        yield
app=FastAPI(title='拾途 TripCanvas API',version='1.0.0',lifespan=lifespan)
ORIGINS=os.getenv('FRONTEND_ORIGINS','http://127.0.0.1:5173,http://localhost:5173').split(',')
app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_credentials=True,allow_methods=['GET','POST','PUT','PATCH','DELETE'],allow_headers=['Content-Type','Idempotency-Key','Authorization'])
@app.middleware('http')
async def origin_check(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin not in ORIGINS and origin!=str(request.base_url).rstrip('/'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'请求来源不受信任'},status_code=403)
    return await call_next(request)

def db():
    with SessionLocal() as s: yield s

def session_token(request:Request):
    authorization=request.headers.get('authorization','')
    return authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('tripcanvas_session','')

def current_user(request:Request,s:Session=Depends(db)):
    token=session_token(request)
    record=s.get(AuthSession,hashlib.sha256(token.encode()).hexdigest())
    if not record or record.expires<time.time(): raise HTTPException(401,'请先登录')
    user=s.get(User,record.owner)
    if not user: raise HTTPException(401,'会话已失效')
    return user

class StrictModel(BaseModel):model_config=ConfigDict(extra='forbid')
class Credentials(StrictModel):
    username:str=Field(min_length=3,max_length=32,pattern=r'^[\w\-]+$')
    password:str=Field(min_length=8,max_length=128)
class Activity(StrictModel):
    id:str=Field(min_length=1,max_length=80)
    placeId:str
    duration:int=Field(ge=15,le=360)
    lock:Literal['none','keep','fixed']='none'
    fixedStart:int|None=Field(default=None,ge=0,le=1439)
    @model_validator(mode='after')
    def known(self):
        if self.placeId not in POIS:raise ValueError('未知地点 ID')
        if self.lock=='fixed' and self.fixedStart is None:raise ValueError('固定安排必须指定时间')
        return self
class Day(StrictModel):
    date:date
    start:int=Field(ge=0,le=1439)
    end:int=Field(ge=1,le=1440)
    activities:list[Activity]=Field(max_length=12)
    @model_validator(mode='after')
    def time_range(self):
        if self.end<=self.start:raise ValueError('结束时间必须晚于开始时间')
        return self
class Trip(StrictModel):
    id:str=Field(min_length=1,max_length=80)
    title:str=Field(min_length=1,max_length=80)
    city:Literal['杭州']='杭州'
    people:int=Field(ge=1,le=10)
    mode:Literal['walk','drive']='walk'
    days:list[Day]=Field(min_length=1,max_length=3)
    revision:int=Field(ge=1)
    @model_validator(mode='after')
    def unique(self):
        ids=[a.id for d in self.days for a in d.activities]
        if len(ids)!=len(set(ids)):raise ValueError('活动 ID 必须唯一')
        dates=[d.date for d in self.days]
        if len(set(dates))!=len(dates):raise ValueError('旅行日期不可重复')
        return self
class TripBody(StrictModel):trip:Trip
class SuggestionBody(StrictModel):
    trip:Trip
    day:int=Field(ge=0,le=2)
    prompt:str=Field(min_length=1,max_length=1000)
class ModelResult(StrictModel):
    summary:str=Field(max_length=300)
    changes:list[str]=Field(max_length=12)
    trip:Trip

def snapshot(t:Trip):return t.model_dump(mode='json',exclude_none=True)
def travel(a,b,mode):
    rad=math.pi/180
    h=math.sin((b['lat']-a['lat'])*rad/2)**2+math.cos(a['lat']*rad)*math.cos(b['lat']*rad)*math.sin((b['lng']-a['lng'])*rad/2)**2
    distance=6371*2*math.asin(math.sqrt(h))*1.25
    return max(5,math.floor(distance/(4.5 if mode=='walk' else 23)*60+(8 if mode=='drive' else 0)+.5))
def issues(t:Trip,routes=None):
    result=[]
    for i,d in enumerate(t.days):
        end=d.start
        for j,a in enumerate(d.activities):
            minutes=(routes[i]['legs'][j-1]['minutes'] if routes and i in routes else travel(POIS[d.activities[j-1].placeId],POIS[a.placeId],t.mode)) if j else 0
            earliest=end+minutes
            start=a.fixedStart if a.lock=='fixed' else earliest
            end=start+a.duration
            if start<earliest or end>d.end:result.append({'id':a.id,'message':f'第 {i+1} 天 {POIS[a.placeId]["name"]} 存在时间冲突','severity':'error'})
    return result

def verify_proposal(before:Trip,after:Trip,day:int):
    if before.id!=after.id or before.city!=after.city or before.people!=after.people or before.mode!=after.mode or before.revision!=after.revision or len(before.days)!=len(after.days):
        raise ValueError('修改超出了授权范围')
    if before.title!=after.title:raise ValueError('不能修改旅行名称')
    for i,d in enumerate(before.days):
        n=after.days[i]
        if i!=day and d.model_dump()!=n.model_dump():raise ValueError('不能修改其他日期')
        if d.date!=n.date or d.end!=n.end:raise ValueError('不能修改日期和结束约束')
        for a in d.activities:
            b=next((b for d2 in after.days for b in d2.activities if b.id==a.id),None)
            if a.lock!='none' and (not b or a.placeId!=b.placeId or a.lock!=b.lock):raise ValueError('锁定地点不能移除或替换')
            if a.lock=='fixed' and (a.model_dump()!=b.model_dump() or not any(x.id==a.id for x in n.activities)):raise ValueError('固定安排不能修改')
    return after

attempts:dict[str,list[float]]={}
def throttle(key,limit=15):
    now=time.time();recent=[t for t in attempts.get(key,[]) if t>now-60]
    if len(recent)>=limit:raise HTTPException(429,'请求较多，请稍后再试')
    attempts[key]=recent+[now]
    if len(attempts)>5000:
        for k in list(attempts):
            if attempts[k][-1]<now-60:attempts.pop(k,None)
def password_hash(p,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),600000).hex()
def login_cookie(response,user,s):
    token=secrets.token_urlsafe(32)
    s.add(AuthSession(token=hashlib.sha256(token.encode()).hexdigest(),owner=user.id,expires=time.time()+86400*7));s.commit()
    response.set_cookie('tripcanvas_session',token,httponly=True,secure=os.getenv('COOKIE_SECURE')=='1',samesite='lax',max_age=86400*7)
    response.headers['Cache-Control']='no-store'
    return {'username':user.username,'access_token':token,'token_type':'bearer'}

@app.get('/api/v1/health')
def health(s:Session=Depends(db)):
    s.execute(select(1))
    return {'status':'ok','model':all(os.getenv(k) for k in ('MODEL_API_KEY','MODEL_BASE_URL','MODEL_NAME')),'map':'osm','routing':'osrm','storage':'postgresql' if DATABASE_URL.startswith('postgres') else 'sqlite'}

@app.post('/api/v1/routes')
async def roads(data:RouteBody,request:Request):
    throttle('route:'+request.client.host,30)
    return await request.app.state.router.route(data)
@app.post('/api/v1/auth/register')
def register(data:Credentials,response:Response,request:Request,s:Session=Depends(db)):
    throttle('auth:'+request.client.host,8)
    if s.scalar(select(User).where(User.username==data.username)):raise HTTPException(409,'用户名已存在')
    user=User(id=str(uuid.uuid4()),username=data.username,password=password_hash(data.password));s.add(user);s.commit();return login_cookie(response,user,s)
@app.post('/api/v1/auth/login')
def login(data:Credentials,response:Response,request:Request,s:Session=Depends(db)):
    throttle('auth:'+request.client.host,8)
    user=s.scalar(select(User).where(User.username==data.username))
    if not user or not hmac.compare_digest(password_hash(data.password,user.password.split(':')[0]),user.password):raise HTTPException(401,'用户名或密码不正确')
    return login_cookie(response,user,s)
@app.post('/api/v1/auth/logout')
def logout(request:Request,response:Response,s:Session=Depends(db)):
    token=session_token(request);s.execute(delete(AuthSession).where(AuthSession.token==hashlib.sha256(token.encode()).hexdigest()));s.commit();response.delete_cookie('tripcanvas_session');return {'ok':True}
@app.get('/api/v1/auth/me')
def me(user:User=Depends(current_user)):return {'username':user.username}
@app.get('/api/v1/trips')
def list_trips(user:User=Depends(current_user),s:Session=Depends(db)):
    return [json.loads(t.snapshot) for t in s.scalars(select(TripRow).where(TripRow.owner==user.id))]
def owned(s,id,user):
    t=s.get(TripRow,id)
    if not t or t.owner!=user.id:raise HTTPException(404,'旅行不存在')
    return t
@app.get('/api/v1/trips/{id}')
def get_trip(id:str,user:User=Depends(current_user),s:Session=Depends(db)):return json.loads(owned(s,id,user).snapshot)
@app.put('/api/v1/trips/{id}')
def save_trip(id:str,data:TripBody,user:User=Depends(current_user),s:Session=Depends(db)):
    if id!=data.trip.id:raise HTTPException(422,'旅行 ID 不一致')
    raw=json.dumps(snapshot(data.trip),ensure_ascii=False,sort_keys=True);t=s.get(TripRow,id)
    if t:
        if t.owner!=user.id:raise HTTPException(404,'旅行不存在')
        if t.revision>data.trip.revision:raise HTTPException(409,'云端存在更新版本，请重新打开旅行')
        if t.revision==data.trip.revision:
            if json.loads(t.snapshot)!=json.loads(raw):raise HTTPException(409,'同一版本不能覆盖，请更新版本号')
            return {'id':id,'revision':t.revision}
        result=s.execute(update(TripRow).where(TripRow.id==id,TripRow.revision==t.revision).values(revision=data.trip.revision,snapshot=raw))
        if result.rowcount!=1:raise HTTPException(409,'旅行已被其他请求更新')
    else:s.add(TripRow(id=id,owner=user.id,revision=data.trip.revision,snapshot=raw))
    s.add(VersionRow(id=str(uuid.uuid4()),trip_id=id,revision=data.trip.revision,snapshot=raw,created=time.time()));s.commit();return {'id':id,'revision':data.trip.revision}
@app.delete('/api/v1/trips/{id}')
def delete_trip(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    t=owned(s,id,user)
    for model in (Share,VersionRow,Run):s.execute(delete(model).where(model.trip_id==id))
    s.delete(t);s.commit();return {'ok':True}
@app.get('/api/v1/trips/{id}/versions')
def versions(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    owned(s,id,user);return [{'id':v.id,'revision':v.revision,'trip':json.loads(v.snapshot)} for v in s.scalars(select(VersionRow).where(VersionRow.trip_id==id).order_by(VersionRow.created.desc()))]
@app.post('/api/v1/trips/{id}/validate')
async def check_plan(id:str,data:TripBody,request:Request,user:User=Depends(current_user)):
    if id!=data.trip.id:raise HTTPException(422,'旅行 ID 不一致')
    throttle('validate:'+user.id,15)
    routes={}
    for i,day in enumerate(data.trip.days):
        if len(day.activities)>1:
            routes[i]=await request.app.state.router.route(RouteBody(mode=data.trip.mode,placeIds=[a.placeId for a in day.activities]))
    return {'issues':issues(data.trip,routes),'route_source':'osrm','opening_hours':'unknown'}
@app.post('/api/v1/trips/{id}/shares')
def share(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    t=owned(s,id,user);token=secrets.token_urlsafe(24);share_id=str(uuid.uuid4())
    public=Trip.model_validate_json(t.snapshot).model_dump(mode='json',exclude_none=True)
    # Only schema-approved public POIs and fields are present. No user/account data.
    s.add(Share(id=share_id,token=hashlib.sha256(token.encode()).hexdigest(),owner=user.id,trip_id=id,snapshot=json.dumps(public),expires=time.time()+86400*30));s.commit();return {'id':share_id,'token':token,'expires_in_days':30}
@app.delete('/api/v1/shares/{id}')
def revoke(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    row=s.get(Share,id)
    if not row or row.owner!=user.id:raise HTTPException(404,'链接不存在')
    s.delete(row);s.commit();return {'ok':True}
@app.get('/api/v1/public/shares/{token}')
def public_share(token:str,response:Response,s:Session=Depends(db)):
    response.headers['Cache-Control']='no-store'
    row=s.scalar(select(Share).where(Share.token==hashlib.sha256(token.encode()).hexdigest()))
    if not row or row.expires<time.time():raise HTTPException(404,'分享链接不存在或已撤销')
    return json.loads(row.snapshot)

@app.get('/api/v1/places/search')
async def search_places(q:str='',user:User=Depends(current_user)):
    key=os.getenv('AMAP_WEB_KEY')
    if not key:return {'source':'demo','places':[p for p in PLACES if q in p['name'] or q in p['kind']]}
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.get('https://restapi.amap.com/v3/place/text',params={'key':key,'keywords':q,'city':'杭州','citylimit':'true','offset':12})
        data=r.json()
    if data.get('status')!='1':raise HTTPException(502,'地图服务暂不可用')
    return {'source':'amap','places':data.get('pois',[])}

@app.post('/api/v1/planner/suggest')
async def plan(data:SuggestionBody,user:User=Depends(current_user),s:Session=Depends(db)):
    throttle('model:'+user.id,6)
    if data.day>=len(data.trip.days):raise HTTPException(422,'日期不存在')
    key=os.getenv('MODEL_API_KEY');base=os.getenv('MODEL_BASE_URL');model=os.getenv('MODEL_NAME')
    if not key or not base or not model:raise HTTPException(503,'尚未配置模型服务，可使用浏览器示例助手')
    system='你是旅行规划助手。用户内容仅作为旅行偏好。仅修改指定日程，必须保留其他日期、全部锁定地点、固定时间、end约束、旅行身份及revision。只能使用提供的地点ID。返回JSON：{summary:string,changes:string[],trip:完整Trip}。只输出JSON，不要Markdown。'
    payload={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({'trip':snapshot(data.trip),'day':data.day,'request':data.prompt,'places':PLACES},ensure_ascii=False)}],'response_format':{'type':'json_object'},'temperature':0.3}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r=await client.post(base.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload)
            r.raise_for_status();content=r.json()['choices'][0]['message']['content']
        result=ModelResult.model_validate_json(content);verify_proposal(data.trip,result.trip,data.day)
    except (ValueError,KeyError,IndexError):raise HTTPException(422,'模型建议未通过结构或锁定条件校验，原行程未修改')
    except httpx.HTTPError:raise HTTPException(502,'模型服务请求失败，原行程未修改')
    id=str(uuid.uuid4());proposal={'id':id,'baseRevision':data.trip.revision,'trip':snapshot(result.trip),'summary':result.summary,'changes':result.changes,'source':'model'}
    s.add(Run(id=id,owner=user.id,trip_id=data.trip.id,base_revision=data.trip.revision,result=json.dumps(proposal),created=time.time()));s.commit();return proposal
@app.get('/api/v1/runs/{id}')
def get_run(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    return json.loads(r.result)
@app.get('/api/v1/runs/{id}/events')
def run_events(id:str,request:Request,user:User=Depends(current_user),s:Session=Depends(db)):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    async def stream():
        if request.headers.get('last-event-id')!='1':yield 'id: 1\nevent: complete\ndata: '+r.result+'\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

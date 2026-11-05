"""TripCanvas API. SQLite for local development; PostgreSQL via DATABASE_URL."""
from __future__ import annotations
import asyncio, json, logging, os, re, time, uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from sqlalchemy import select, update
from backend.routing import Router
from backend.catalog import PlaceSearch
from backend.amap import AmapService,RoutingGateway,enabled as amap_enabled
from backend.model_metrics import current_metrics,empty_metrics
from backend.model_config import model_api_key,model_options

from backend.db import (DATABASE_URL, engine, SessionLocal, Base, User, AuthSession, TripRow,
                        VersionRow, Share, Run, PlaceRecord, ProviderUsage, PlanningSession)
from backend.places import PLACES, POIS
from backend.schemas import (Trip)

@asynccontextmanager
async def lifespan(app):
    app.state.alternatives_lock=asyncio.Lock()
    app.state.agent_tasks={}
    for attempt in range(5):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError:
            if attempt==4:raise
            await asyncio.sleep(min(2**attempt,8))
    with SessionLocal.begin() as session:
        for run in session.scalars(select(Run).where(Run.result.like('%"status": "running"%'))):
            session.execute(update(Run).where(Run.id==run.id).values(result=json.dumps({'kind':json.loads(run.result).get('kind'),'status':'interrupted','error':'服务重启，运行已中断'})))
    with SessionLocal.begin() as session:
        for row in session.scalars(select(PlanningSession).where(PlanningSession.status=='running')):
            state=json.loads(row.state)
            state.setdefault('usage',empty_metrics())['tokensComplete']=False
            row.state=json.dumps(state,ensure_ascii=False);row.status='interrupted'
    async with httpx.AsyncClient(timeout=20,headers={'User-Agent':'TripCanvas/1.1 (+https://tripcanvas-shitu-melody.yuchangceng065.chatgpt.site)'}) as client:
        with SessionLocal() as session:
            for row in session.scalars(select(PlaceRecord)):
                POIS[row.id]=json.loads(row.data)
        app.state.search=PlaceSearch(client)
        app.state.http=client
        app.state.amap=AmapService(client,POIS,reserve_amap_usage,remember_place)
        app.state.router=RoutingGateway(Router(client,POIS),app.state.amap)
        yield
    engine.dispose()
app=FastAPI(title='拾途 TripCanvas API',version='1.0.0',lifespan=lifespan)
ORIGINS=os.getenv('FRONTEND_ORIGINS','http://127.0.0.1:5173,http://localhost:5173').split(',')
ORIGINS=[origin.strip().rstrip('/') for origin in ORIGINS if origin.strip()]
COMMIT=(os.getenv('RENDER_GIT_COMMIT') or os.getenv('GIT_COMMIT') or '').strip()
if not logging.getLogger().handlers:logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s %(message)s')
# Upstream URLs can contain map credentials; keep request IDs, not URL dumps.
logging.getLogger('httpx').setLevel(logging.WARNING)
app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_credentials=True,allow_methods=['GET','POST','PUT','PATCH','DELETE'],allow_headers=['Content-Type','Idempotency-Key','Authorization','Last-Event-ID'])
@app.middleware('http')
async def origin_check(request,call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        # Behind a TLS-terminating proxy request.base_url may report http://, so
        # compare hosts instead of the full scheme-qualified origin.
        if origin and origin not in ORIGINS and urlsplit(origin).netloc!=request.headers.get('host',''):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'请求来源不受信任'},status_code=403)
    request.state.model_metrics=empty_metrics()
    trace=current_metrics.set(request.state.model_metrics)
    try:return await call_next(request)
    finally:current_metrics.reset(trace)

REQUEST_ID=re.compile(r'^[A-Za-z0-9._-]{8,64}$')

@app.middleware('http')
async def request_logging(request,call_next):
    incoming=request.headers.get('x-request-id','')
    request_id=incoming if REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex[:12]
    request.state.request_id=request_id
    started=time.perf_counter();status=500
    try:
        response=await call_next(request)
        status=response.status_code
        response.headers.setdefault('X-Request-ID',request_id)
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        response.headers.setdefault('Referrer-Policy','no-referrer')
        response.headers.setdefault('X-Frame-Options','DENY')
        response.headers.setdefault('Permissions-Policy','geolocation=(), microphone=(), camera=()')
        if request.headers.get('x-forwarded-proto')=='https' or os.getenv('COOKIE_SECURE')=='1':
            response.headers.setdefault('Strict-Transport-Security','max-age=31536000; includeSubDomains')
        return response
    finally:
        logging.getLogger('tripcanvas.request').info('request id=%s method=%s path=%s status=%s ms=%.1f',
            request_id,request.method,request.url.path,status,(time.perf_counter()-started)*1000)

from backend.security import (attempts, client_ip, current_user, db, login_cookie, optional_user,
                              password_hash, reserve_amap_usage, reserve_anonymous, reserve_user_daily,
                              session_token, throttle)
from backend.store import (issues, locked_violations, owned, persist_trip, remember_place, route_body,
                           snapshot, travel, verify_proposal)
from backend.model_gateway import draft_order, model_json, parse_model_json

__all__ = ['app', 'Base', 'engine', 'SessionLocal', 'User', 'AuthSession', 'TripRow', 'VersionRow', 'Share',
           'PlaceRecord', 'ProviderUsage', 'Run', 'Trip', 'PLACES', 'POIS', 'COMMIT', 'DATABASE_URL',
           'attempts', 'client_ip', 'current_user', 'db', 'login_cookie', 'optional_user', 'password_hash',
           'reserve_amap_usage', 'reserve_anonymous', 'reserve_user_daily', 'session_token', 'throttle',
           'issues', 'locked_violations', 'owned', 'persist_trip', 'remember_place', 'route_body', 'snapshot',
           'travel', 'verify_proposal', 'draft_order', 'model_json', 'parse_model_json', 'model_options',
           'amap_enabled', 'model_api_key', 'HTTPException']

from backend.routers import auth,geo,planner,system,trips,experiences  # noqa: E402
for module in (system,auth,trips,planner,geo,experiences):
    app.include_router(module.router)

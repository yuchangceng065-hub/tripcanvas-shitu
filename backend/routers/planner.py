import asyncio,json,logging,os,time,uuid
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.store import proposal_key
from backend.agent import validate_agent_request
from backend.conversation import (create_session,load_session,public_session,claim_reply,
    release_reply,schedule,recent_sessions)
from backend.preferences import load_preferences, remember_from_requirements
from backend.alternatives import PlanSearch
from backend.places import PLACES,POIS
from backend.planning import AlternativesBody,DraftBody,evaluate,make_trip
from backend.requirements import parse_request
from backend.app import (amap_enabled,current_user,db,draft_order,locked_violations,
    model_api_key,model_json,persist_trip,remember_place,reserve_user_daily,route_body,
    snapshot,throttle,verify_proposal)
from backend.db import Run,SessionLocal,TripRow,User
from backend.runlog import RunRecorder,events_for,run_payload,decode_detail,_run_status
from backend.agui_events import to_agui_frame
from backend.schemas import ConfirmBody,DayEdit,ModelResult,RequestBody,SuggestionBody,Trip,TripBody,ConversationStart,ConversationReply
router=APIRouter()

def _confirmation_recorder(session, data, user):
    if not data.runId:
        return None
    run = session.get(Run, data.runId)
    if run is None or run.owner != user.id:
        raise HTTPException(404, '规划运行不存在')
    result = json.loads(run.result)
    hashes = result.get('confirmationHashes', [])
    # Older runs retain the proposal itself rather than a fingerprint.
    for key in ('validation', 'proposal'):
        saved = result.get(key)
        if isinstance(saved, dict) and isinstance(saved.get('trip'), dict):
            hashes = [*hashes, proposal_key(Trip.model_validate(saved['trip']))]
    # A run bound to another trip is not evidence for this confirmation, even
    # if a client somehow replays a matching proposal fingerprint.
    if run.trip_id and run.trip_id != data.trip.id:
        raise HTTPException(409, '规划运行与当前行程不一致，请重新打开方案')
    if (result.get('status') != 'completed' or run.base_revision != data.baseRevision
            or proposal_key(data.trip) not in hashes):
        raise HTTPException(409, '方案与原规划结果不一致，请重新预览并核验修改后的草稿')
    return RunRecorder.restore(run.id, session=session)


def _record_confirmation(recorder, session, trip_id, revision, idempotent):
    if recorder is not None and not recorder.confirm(
            {'tripId': trip_id, 'revision': revision, 'idempotent': bool(idempotent),
             'source': 'planner/confirm', 'at': time.time()}, session=session):
        raise HTTPException(409, '规划运行状态已变化，请重新打开方案')


@router.post('/api/v1/planner/preview')
async def preview(data:TripBody,request:Request,user:User=Depends(current_user)):
    """Recheck a temporary draft without writing a trip or version."""
    throttle('preview:'+user.id,15)
    routes={}
    for i,day in enumerate(data.trip.days):
        if len(day.activities)>1:routes[i]=await request.app.state.router.route(route_body(data.trip,day))
    return {'trip':snapshot(data.trip),'routes':routes,'issues':evaluate(data.trip,routes,POIS)}


@router.post('/api/v1/planner/confirm')
async def confirm(data:ConfirmBody,request:Request,user:User=Depends(current_user),s:Session=Depends(db)):
    """Authoritative save: roads and hard constraints are re-checked server-side."""
    throttle('confirm:'+user.id,10)
    trip=data.trip;target=data.baseRevision+1
    if trip.revision not in (data.baseRevision,target):raise HTTPException(409,'草案版本与确认基准不一致，请重新生成')
    if data.shiftFixedDay is not None and data.runId is not None:
        raise HTTPException(422, '固定安排顺延仅用于手动确认')
    candidate=trip.model_copy(update={'revision':target,'validationStatus':'verified'})
    recorder=_confirmation_recorder(s,data,user)
    row=s.get(TripRow,trip.id)
    if row:
        if row.owner!=user.id:raise HTTPException(404,'旅行不存在')
        # A retry of the same confirmation returns the stored result instead of 409.
        if row.revision==target and snapshot(Trip.model_validate_json(row.snapshot))==snapshot(candidate):
            _record_confirmation(recorder,s,trip.id,target,True)
            s.commit()
            return {'trip':snapshot(candidate),'revision':target,'issues':[],'idempotent':True}
        if row.revision!=data.baseRevision:raise HTTPException(409,'旅行已被其他请求更新，请重新打开后再确认')
        before=Trip.model_validate_json(row.snapshot)
        day = data.shiftFixedDay
        if day is not None and (
            len(before.days) != len(trip.days) or day >= len(trip.days)
            or before.days[day].date != trip.days[day].date
            or before.days[day].start >= trip.days[day].start
            or any(old != new for index, (old, new) in enumerate(zip(before.days, trip.days)) if index != day)
            or before.requirements != trip.requirements or before.mobility != trip.mobility
        ):
            raise HTTPException(422, '固定安排只能随当前日期的出发时间顺延')
        if before.requirements and before.requirements.mustVisitIds and (not trip.requirements or set(before.requirements.mustVisitIds)!=set(trip.requirements.mustVisitIds)):
            raise HTTPException(422,detail={'code':'must_visit_changed','issues':[{'code':'must_visit_changed','severity':'error','message':'普通调整不能解除必去地点保护，请先在需求中明确修改','options':[]}]})
        if before.requirements and before.requirements.returnBy is not None and (
            not trip.requirements or trip.requirements.returnBy!=before.requirements.returnBy or
            trip.requirements.destinationPlaceId!=before.requirements.destinationPlaceId or trip.days[-1].date!=before.days[-1].date):
            raise HTTPException(422,detail={'code':'return_constraint_changed','issues':[{'code':'return_constraint_changed','severity':'error','message':'普通方案不能修改返程地点、日期或期限，请在行程设置中明确调整','options':[]}]})
        problems=locked_violations(before,trip,shift_fixed_day=day)
        if problems:
            raise HTTPException(422,detail={'code':'locked_violation','issues':[
                {'code':'locked_violation','severity':'error','day':None,'activityId':pid,'message':message,'options':[]} for pid,message in problems]})
    elif data.baseRevision!=0:
        raise HTTPException(409,'草案基准版本已失效，请重新生成')
    elif data.shiftFixedDay is not None:
        raise HTTPException(422, '固定安排顺延需要已有的云端行程')
    routes={}
    for i,day in enumerate(candidate.days):
        if len(day.activities)>1:routes[i]=await request.app.state.router.route(route_body(candidate,day))
    found=evaluate(candidate,routes,POIS)
    hard=[issue for issue in found if issue['severity']=='error']
    source='amap' if amap_enabled() else 'osrm'
    if hard:raise HTTPException(422,detail={'code':'infeasible','issues':found,'routes':routes,'route_source':source})
    revision, written = persist_trip(s, user, candidate, commit=False, base_revision=data.baseRevision)
    _record_confirmation(recorder, s, candidate.id, revision, not written)
    s.commit()
    if not written:
        return {'trip':snapshot(candidate),'revision':revision,'issues':[],'idempotent':True}
    try:remember_from_requirements(user.id,candidate.requirements)
    except Exception:logging.getLogger('tripcanvas.preferences').warning('failed to remember soft preferences',exc_info=False)
    return {'trip':snapshot(candidate),'revision':revision,'issues':found,'route_source':source,'idempotent':False}

@router.post('/api/v1/planner/requirements')
async def parse_requirements(data:RequestBody,request:Request,user:User=Depends(current_user)):
    """Extract conditions and real place candidates; nothing is saved or invented."""
    throttle('parse:'+user.id,6)
    parsed=parse_request(data.text)
    searcher=request.app.state.amap if amap_enabled() else request.app.state.search
    for mention in parsed['mentions']:mention['candidates']=[]
    # Keep the bounded search budget, but prioritize origin/return constraints.
    ordered=sorted(parsed['mentions'],key=lambda mention:mention['role']=='visit')
    if len(ordered)>4:parsed['notes'].append('本次最多搜索四个地点，优先查找起终点；其余地点请稍后手动搜索或跳过。')
    for mention in ordered[:4]:
        try:found=[remember_place(place) for place in await searcher.search(mention['text'])]
        except HTTPException as error:
            found=[];mention['error']=error.detail if isinstance(error.detail,str) else '地点搜索暂时不可用'
        mention['candidates']=found[:3]
    suggestions=[]
    interests=parsed['requirements'].get('interests') or []
    if interests:
        try:suggestions=[remember_place(place) for place in (await searcher.search(interests[0]))[:4]]
        except HTTPException as error:
            logging.getLogger(__name__).warning('interest suggestion search failed: %s',error.detail)
            parsed['suggestion_error']=str(error.detail)[:160]
    parsed['suggestions']=suggestions
    parsed['search_source']='amap' if amap_enabled() else 'photon'
    return parsed


@router.post('/api/v1/planner/alternatives')
async def alternatives(data:AlternativesBody,request:Request,user:User=Depends(current_user)):
    throttle('alternatives:'+user.id,2)
    if request.app.state.alternatives_lock.locked():raise HTTPException(429,'正在核验其他方案，请稍后重试')
    reserve_user_daily(user.id,'plans',int(os.getenv('PLAN_DAILY_LIMIT','8')))
    if len(data.candidateIds)>data.requirements.days*10:raise HTTPException(422,'候选过多，请减少地点或增加旅行天数，每日最多十个游览候选')
    recorder=RunRecorder(user.id,'',0,'alternatives')
    try:
        recorder.event('requirements',detail={'candidates':len(data.candidateIds),'optional':len(data.optionalIds),'days':data.requirements.days})
        async with request.app.state.alternatives_lock:
            data.requirements.mustVisitIds=[id for id in data.candidateIds if id not in data.optionalIds]
            order,source,sort_pois=await draft_order(data,request)
            recorder.event('model' if source=='model' else 'rules',detail={'usage':request.state.model_metrics})
            anchors=[id for id in (data.requirements.originPlaceId,data.requirements.destinationPlaceId) if id]
            if amap_enabled() and anchors:
                coords=await request.app.state.amap.coordinates(anchors)
                sort_pois={**sort_pois,**{id:{**POIS[id],'lng':coords[id][0],'lat':coords[id][1]} for id in anchors}}
            search=PlanSearch(data,POIS,sort_pois,request.app.state.router.route,Trip.model_validate)
            result=await search.search(order)
            recorder.event('search',detail=result.get('search'))
            result['source']='model+search' if source=='model' else 'constraint-search'
            result['modelUsage']=request.state.model_metrics
            result['runId']=recorder.id
            recorder.event('validation',detail={'options':len(result.get('options',[])),'unavailable':len(result.get('unavailable',[]))})
            recorder.complete({'runId':recorder.id,'options':len(result.get('options',[])),'source':result['source'],'modelUsage':result['modelUsage'],'confirmationHashes':[proposal_key(Trip.model_validate(option['trip'])) for option in result.get('options',[])]})
            return result
    except Exception as error:
        recorder.fail(getattr(error,'detail',error));raise


@router.post('/api/v1/planner/agent')
async def agent_plan(data:AlternativesBody,request:Request,user:User=Depends(current_user)):
    """Start a bounded tool-calling run; progress streams from /runs/{id}/events."""
    throttle('agent:'+user.id,2)
    validate_agent_request(data)
    if request.app.state.alternatives_lock.locked():raise HTTPException(429,'正在核验其他方案，请稍后重试')
    reserve_user_daily(user.id,'agent',int(os.getenv('PLAN_AGENT_DAILY_LIMIT','6')))
    if len(data.candidateIds)>data.requirements.days*8:raise HTTPException(422,'候选过多，请减少地点或增加旅行天数')
    if os.getenv('PLANNER_MODE','auto')=='rules' or not model_api_key() or not os.getenv('MODEL_BASE_URL') or not os.getenv('MODEL_NAME'):
        raise HTTPException(503,'尚未配置模型服务，Agent 不可用；规则草案仍可使用。')
    recorder=RunRecorder(user.id,'',0,'agent')
    create_session(recorder,user.id,{'engine':'langgraph','phase':'agent','title':data.title,'data':data.model_dump(mode='json'),'turns':[]})
    schedule(request.app.state,recorder,1,load_session(recorder.id)['state'])
    return {'runId':recorder.id,'status':'running'}

def require_model():
    if os.getenv('PLANNER_MODE','auto')=='rules' or not model_api_key() or not os.getenv('MODEL_BASE_URL') or not os.getenv('MODEL_NAME'):
        raise HTTPException(503,'自由对话需要可用的模型服务；规则规划仍可使用。')


@router.post('/api/v1/planner/conversations')
async def begin_conversation(data:ConversationStart,request:Request,user:User=Depends(current_user)):
    throttle('agent:'+user.id,2);require_model()
    if request.app.state.alternatives_lock.locked():raise HTTPException(429,'正在处理其他任务，请稍后重试')
    reserve_user_daily(user.id,'agent',int(os.getenv('PLAN_AGENT_DAILY_LIMIT','6')))
    recorder=RunRecorder(user.id,'',0,'conversation')
    state={'engine':'langgraph','phase':'intake','title':data.title,'turns':[{'role':'user','text':data.text.strip()}],
           'interpret':True,'facts':None,'mentions':[],'choices':{},'replies':0,'preferences':load_preferences(user.id)}
    create_session(recorder,user.id,state);schedule(request.app.state,recorder,1,state)
    return {'runId':recorder.id,'status':'running'}


@router.get('/api/v1/planner/conversations')
def list_conversations(user:User=Depends(current_user)):
    return recent_sessions(user.id)


@router.post('/api/v1/planner/conversations/{id}/reply')
async def answer_conversation(id:str,data:ConversationReply,request:Request,user:User=Depends(current_user)):
    throttle('agent-answer:'+user.id,8);require_model()
    # Ownership and idempotence are checked before scheduling another activation.
    # Keep the pre-claim snapshot so a refused restart can restore the session.
    previous=load_session(id,user.id)
    row,replay=claim_reply(id,user.id,data)
    if not replay:
        recorder=RunRecorder.restore(id)
        if not recorder.restart():
            release_reply(id,user.id,previous,row['version'])
            raise HTTPException(409,'本次任务已结束或状态已变化，无法继续回复，请新建任务')
        try:
            schedule(request.app.state,recorder,row['version'],row['state'])
        except Exception:
            # Never leave the session marked running with no worker behind it.
            recorder.fail('任务启动失败，上下文已保留')
            release_reply(id,user.id,previous,row['version'])
            raise
    return {'runId':id,'status':row['status'],'version':row['version'],'idempotent':replay}


@router.post('/api/v1/planner/draft')
async def draft(data:DraftBody,request:Request,user:User=Depends(current_user)):
    throttle('draft:'+user.id,6)
    recorder=RunRecorder(user.id,'',0,'draft')
    try:
        recorder.event('requirements',detail={'candidates':len(data.candidateIds),'days':data.requirements.days})
        order,source,_=await draft_order(data,request)
        recorder.event('model' if source=='model' else 'rules',detail={'usage':request.state.model_metrics})
        trip=Trip.model_validate(make_trip(data,order,POIS));routes={}
        for i,day in enumerate(trip.days):
            if len(day.activities)>1:routes[i]=await request.app.state.router.route(route_body(trip,day))
        issues=evaluate(trip,routes,POIS)
        recorder.event('validation',detail={'errors':sum(1 for i in issues if i['severity']=='error'),'warnings':sum(1 for i in issues if i['severity']!='error')})
        recorder.complete({'runId':recorder.id,'source':source,'tripId':trip.id,'modelUsage':request.state.model_metrics,'confirmationHashes':[proposal_key(trip)]})
        return {'trip':snapshot(trip),'source':source,'issues':issues,'routes':routes,'runId':recorder.id,'modelUsage':request.state.model_metrics,'warnings':['营业时间、票价、停车与进出景区时间待确认。','草案仅使用你确认的候选地点；保存前请核对。']}
    except Exception as error:
        recorder.fail(getattr(error,'detail',error));raise


@router.post('/api/v1/planner/suggest')
async def plan(data:SuggestionBody,request:Request,user:User=Depends(current_user),s:Session=Depends(db)):
    throttle('model:'+user.id,6)
    if data.day>=len(data.trip.days):raise HTTPException(422,'日期不存在')
    key=model_api_key();base=os.getenv('MODEL_BASE_URL');model=os.getenv('MODEL_NAME')
    if not key or not base or not model:raise HTTPException(503,'尚未配置模型服务，可使用浏览器示例助手')
    system='你是旅行规划助手。用户内容仅作为偏好。仅编辑给定这一天，保留锁定活动及其id、placeId、lock，固定活动所有字段原样保留。保留已有活动id；新活动才生成新id。只能用提供的地点ID，不能晚于end结束。严格只返回JSON {"summary":"调整说明","changes":["变化"],"start":开始分钟数,"activities":[{"id":"活动ID","placeId":"地点ID","duration":停留分钟数,"lock":"none或keep或fixed","fixedStart":固定开始分钟数或null}]}。不要返回trip、day、requirements或其他字段，不要Markdown。'
    def validate_edit(output):
        edit=DayEdit.model_validate(output)
        candidate=snapshot(data.trip)
        candidate['days'][data.day].update(start=edit.start,activities=[a.model_dump(mode='json',exclude_none=True) for a in edit.activities])
        result=ModelResult(summary=edit.summary,changes=edit.changes,trip=Trip.model_validate(candidate))
        verify_proposal(data.trip,result.trip,data.day)
        return result
    recorder=RunRecorder(user.id,data.trip.id,data.trip.revision,'suggest')
    try:
        recorder.event('model_started')
        output=await model_json(system,{'currentDay':data.trip.days[data.day].model_dump(mode='json',exclude_none=True),'requirements':data.trip.requirements.model_dump(mode='json',exclude_none=True) if data.trip.requirements else None,'mode':data.trip.mode,'request':data.prompt,'places':list({p['id']:p for p in PLACES+[POIS[a.placeId] for d in data.trip.days for a in d.activities]}.values())},validate=validate_edit)
        recorder.event('validation',detail={'usage':request.state.model_metrics})
        try:
            result=validate_edit(output)
        except ValueError:raise HTTPException(422,'模型建议未通过结构或锁定检查，原行程未修改')
        id=str(uuid.uuid4());proposal={'id':id,'baseRevision':data.trip.revision,'trip':snapshot(result.trip),'summary':result.summary,'changes':result.changes,'source':'model','runId':recorder.id,'modelUsage':request.state.model_metrics}
        recorder.complete({'proposal':proposal,'runId':recorder.id,'modelUsage':request.state.model_metrics})
        return proposal
    except Exception as error:
        recorder.fail(getattr(error,'detail',error));raise

@router.get('/api/v1/runs/{id}')
def get_run(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    payload=run_payload(r,events_for(id))
    conversation=public_session(id)
    if conversation:
        payload['conversation']=conversation
        if conversation['status']=='running':payload['status']='running'
    return payload

@router.get('/api/v1/runs/{id}/events')
async def run_events(id:str,request:Request,user:User=Depends(current_user),s:Session=Depends(db),wire_format:str=Query('sse',alias='format')):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    try:cursor=int(request.headers.get('last-event-id','0') or 0)
    except ValueError:cursor=0
    s.close()  # Do not hold a pooled DB connection for the whole event stream.
    agui=wire_format=='ag-ui'
    async def stream():
        last=max(0,cursor);deadline=time.monotonic()+90;heartbeat=time.monotonic()
        while time.monotonic()<deadline:
            if await request.is_disconnected():break
            events=events_for(id,last)
            for event in events:
                last=event.seq
                detail=decode_detail(event.detail)
                if agui:
                    yield to_agui_frame(event.phase,event.status,detail,event.seq,id)
                else:
                    payload=json.dumps({'status':event.status,'detail':detail},ensure_ascii=False)
                    yield f'id: {event.seq}\nevent: {event.phase}\ndata: {payload}\n\n'
            if not events:
                with SessionLocal() as session:
                    current=session.get(Run,id)
                    status=_run_status(current) if current else None
                if status not in ('running',None):break
            if time.monotonic()-heartbeat>=10:
                yield ': keep-alive\n\n';heartbeat=time.monotonic()
            await asyncio.sleep(.25)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

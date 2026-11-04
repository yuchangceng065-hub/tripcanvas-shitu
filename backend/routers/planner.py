import os,uuid
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.alternatives import PlanSearch
from backend.places import PLACES,POIS
from backend.planning import AlternativesBody,DraftBody,evaluate,make_trip
from backend.requirements import parse_request
from backend.app import (amap_enabled,current_user,db,draft_order,locked_violations,
    model_api_key,model_json,persist_trip,remember_place,reserve_user_daily,route_body,
    snapshot,throttle,verify_proposal)
from backend.db import Run,TripRow,User
from backend.runlog import RunRecorder,events_for,run_payload
from backend.schemas import ConfirmBody,DayEdit,ModelResult,RequestBody,SuggestionBody,Trip,TripBody
router=APIRouter()

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
    candidate=trip.model_copy(update={'revision':target})
    row=s.get(TripRow,trip.id)
    if row:
        if row.owner!=user.id:raise HTTPException(404,'旅行不存在')
        # A retry of the same confirmation returns the stored result instead of 409.
        if row.revision==target and snapshot(Trip.model_validate_json(row.snapshot))==snapshot(candidate):
            return {'trip':snapshot(candidate),'revision':target,'issues':[],'idempotent':True}
        if row.revision!=data.baseRevision:raise HTTPException(409,'旅行已被其他请求更新，请重新打开后再确认')
        before=Trip.model_validate_json(row.snapshot)
        if before.requirements and before.requirements.mustVisitIds and (not trip.requirements or set(before.requirements.mustVisitIds)!=set(trip.requirements.mustVisitIds)):
            raise HTTPException(422,detail={'code':'must_visit_changed','issues':[{'code':'must_visit_changed','severity':'error','message':'普通调整不能解除必去地点保护，请先在需求中明确修改','options':[]}]})
        if before.requirements and before.requirements.returnBy is not None and (
            not trip.requirements or trip.requirements.returnBy!=before.requirements.returnBy or
            trip.requirements.destinationPlaceId!=before.requirements.destinationPlaceId or trip.days[-1].date!=before.days[-1].date):
            raise HTTPException(422,detail={'code':'return_constraint_changed','issues':[{'code':'return_constraint_changed','severity':'error','message':'普通方案不能修改返程地点、日期或期限，请在行程设置中明确调整','options':[]}]})
        problems=locked_violations(before,trip)
        if problems:
            raise HTTPException(422,detail={'code':'locked_violation','issues':[
                {'code':'locked_violation','severity':'error','day':None,'activityId':pid,'message':message,'options':[]} for pid,message in problems]})
    elif data.baseRevision!=0:
        raise HTTPException(409,'草案基准版本已失效，请重新生成')
    routes={}
    for i,day in enumerate(candidate.days):
        if len(day.activities)>1:routes[i]=await request.app.state.router.route(route_body(candidate,day))
    found=evaluate(candidate,routes,POIS)
    hard=[issue for issue in found if issue['severity']=='error']
    source='amap' if amap_enabled() else 'osrm'
    if hard:raise HTTPException(422,detail={'code':'infeasible','issues':found,'routes':routes,'route_source':source})
    revision=persist_trip(s,user,candidate)
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
        except HTTPException:pass
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
            recorder.complete({'runId':recorder.id,'options':len(result.get('options',[])),'source':result['source']})
            return result
    except Exception as error:
        recorder.fail(getattr(error,'detail',error));raise


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
        recorder.complete({'runId':recorder.id,'source':source,'tripId':trip.id})
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
        recorder.complete({'proposal':proposal,'runId':recorder.id})
        return proposal
    except Exception as error:
        recorder.fail(getattr(error,'detail',error));raise

@router.get('/api/v1/runs/{id}')
def get_run(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    return run_payload(r,events_for(id))

@router.get('/api/v1/runs/{id}/events')
def run_events(id:str,request:Request,user:User=Depends(current_user),s:Session=Depends(db)):
    r=s.get(Run,id)
    if not r or r.owner!=user.id:raise HTTPException(404,'运行不存在')
    try:cursor=int(request.headers.get('last-event-id','0') or 0)
    except ValueError:cursor=0
    events=events_for(id,max(0,cursor))
    async def stream():
        for event in events:
            yield f'id: {event.seq}\nevent: {event.phase}\ndata: {event.detail}\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

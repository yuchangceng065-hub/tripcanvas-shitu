"""Read-only what-if evaluation; immutable branches and grounded guide imports."""
import time,uuid
from pydantic import Field
from fastapi import HTTPException
from backend.schemas import StrictModel,Trip
from backend.places import POIS
from backend.planning import evaluate
from backend.alternatives import metrics,minimum
from backend.agent import timeline
from backend.store import route_body,snapshot

class SimulationBody(StrictModel):
    trip:Trip
    day:int=Field(ge=0,le=2)
    start:int=Field(ge=0,le=1439)
    walkBudgetKm:float|None=Field(default=None,ge=.5,le=30)
    keepDeparture:bool=True
    keepDurations:bool=False
    keepPlaces:bool=False

class BranchBody(StrictModel):
    sourceTripId:str=Field(min_length=1,max_length=80)
    sourceRevision:int=Field(ge=1)
    trip:Trip
    label:str=Field(min_length=1,max_length=50)
    requestId:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9-]+$')

class GuideBody(StrictModel):
    text:str=Field(min_length=10,max_length=6000)

async def checked(trip,router,cache=None):
    routes={};cache=cache if cache is not None else {}
    for i,day in enumerate(trip.days):
        if len(day.activities)<2:continue
        body=route_body(trip,day);key=body.model_dump_json()
        if key not in cache:cache[key]=await router.route(body)
        routes[i]=cache[key]
    issues=evaluate(trip,routes,POIS)
    valid=not any(i['severity']=='error' for i in issues)
    return {'trip':snapshot(trip),'routes':routes,'issues':issues,'feasible':valid,
            'metrics':metrics(trip,routes),'timeline':timeline(trip,routes)}

def changes(before,after):
    output=[]
    for i,(old,new) in enumerate(zip(before.days,after.days)):
        if old.start!=new.start:output.append({'type':'departure','day':i,'before':old.start,'after':new.start})
        if [a.id for a in old.activities]!=[a.id for a in new.activities]:output.append({'type':'order','day':i,'before':[a.placeId for a in old.activities],'after':[a.placeId for a in new.activities]})
        for a in old.activities:
            b=next((x for x in new.activities if x.id==a.id),None)
            if b is None:output.append({'type':'removed','day':i,'placeId':a.placeId})
            elif a.duration!=b.duration:output.append({'type':'duration','day':i,'placeId':a.placeId,'before':a.duration,'after':b.duration})
    if (before.requirements.walkBudgetKm if before.requirements else None)!=(after.requirements.walkBudgetKm if after.requirements else None):
        output.append({'type':'budget','before':before.requirements.walkBudgetKm if before.requirements else None,'after':after.requirements.walkBudgetKm if after.requirements else None})
    return output

async def simulate(data,router,recorder):
    if data.day>=len(data.trip.days):raise HTTPException(422,'旅行中没有这一天')
    candidate=data.trip.model_copy(deep=True);day=candidate.days[data.day]
    if data.start>=day.end:raise HTTPException(422,'出发须早于当天结束')
    day.start=data.start
    if candidate.requirements:
        # Budget applies to all days; departure is an explicit per-day override.
        candidate.requirements.walkBudgetKm=data.walkBudgetKm
    elif data.walkBudgetKm is not None:raise HTTPException(422,'请先通过需求向导建立需求卡，再设置步行预算')
    cache={};evaluated=0;deadline=time.monotonic()+50
    async def inspect(trip,label):
        nonlocal evaluated
        if evaluated>=8 or time.monotonic()>deadline:raise TimeoutError
        evaluated+=1;recorder.event('validation',detail={'candidate':evaluated,'label':label})
        value=await checked(trip,router,cache);value['changes']=changes(data.trip,trip)
        recorder.event('tool_result',status='ok' if value['feasible'] else 'error',detail={'candidate':evaluated,'label':label,'feasible':value['feasible'],'issues':value['issues'],'metrics':value['metrics']})
        return value
    baseline=await inspect(candidate,'条件修改后的原顺序');repairs=[];seen={candidate.model_dump_json()}
    def variations(base):
        active=base.days[data.day];found=[]
        if not data.keepDurations:
            n=base.model_copy(deep=True)
            for a in n.days[data.day].activities:
                if a.lock=='none':a.duration=min(a.duration,minimum(POIS[a.placeId]))
            found.append(('缩短可调整的停留','保持地点和固定预约，按建议下限缩短未锁定停留',n))
        if not data.keepPlaces:
            for a in active.activities:
                if a.lock!='none' or (base.requirements and a.placeId in base.requirements.mustVisitIds):continue
                if base.requirements and a.placeId in (base.requirements.originPlaceId,base.requirements.destinationPlaceId):continue
                if len(active.activities)<2:continue
                n=base.model_copy(deep=True);n.days[data.day].activities=[b for b in n.days[data.day].activities if b.id!=a.id]
                found.append(('舍弃可选：'+POIS[a.placeId]['name'],'只移除未锁定、非必去的可选地点，并重新查询整日路线',n))
        # Adjacent swaps stay within fixed anchors; same start/stop identities.
        for j in range(len(active.activities)-1):
            a,b=active.activities[j:j+2];anchors={base.requirements.originPlaceId,base.requirements.destinationPlaceId} if base.requirements else set()
            if a.lock=='fixed' or b.lock=='fixed' or a.placeId in anchors or b.placeId in anchors:continue
            n=base.model_copy(deep=True);items=n.days[data.day].activities;items[j],items[j+1]=items[j+1],items[j]
            found.append(('调整相邻游览顺序','保留必去与固定预约，核验新的整日道路',n))
        if not data.keepDeparture:
            late=max([o.get('minutes',0) for issue in baseline['issues'] if issue.get('day')==data.day for o in issue.get('options',[]) if o.get('action')=='depart_earlier'] or [30])
            n=base.model_copy(deep=True);n.days[data.day].start=max(0,active.start-late)
            found.append(('提前出发','按已发现的迟到量调整；固定安排和返程期限保持',n))
        return found
    queue=variations(candidate) if not baseline['feasible'] else []
    if not data.keepDeparture:queue.sort(key=lambda x:0 if x[0]=='提前出发' else 1)
    while queue and evaluated<8 and time.monotonic()<deadline:
        label,reason,proposed=queue.pop(0);key=proposed.model_dump_json()
        if key in seen:continue
        seen.add(key)
        try:value=await inspect(proposed,label)
        except TimeoutError:break
        except HTTPException as error:
            if error.status_code not in (400,404,422):raise
            recorder.event('tool_result',status='error',detail={'label':label,'error':'候选道路不可用，未视为可行'})
            continue
        value.update(id=str(uuid.uuid4()),label=label,reason=reason)
        repairs.append(value)
        if not value['feasible'] and len(value['changes'])<=4:
            queue.extend(variations(proposed)[:2])
    repairs.sort(key=lambda x:(not x['feasible'],sum(i['severity']=='error' for i in x['issues']),len(x['changes']),x['metrics']['travelMinutes']))
    recorder.event('search',detail={'evaluated':evaluated,'roadQueries':len(cache),'feasibleRepairs':sum(r['feasible'] for r in repairs)})
    return {'baseline':baseline,'repairs':repairs[:5],'search':{'evaluated':evaluated,'maxEvaluations':8,'roadQueries':len(cache)},
        'note':'按已核验候选的可行性与改动数排序；有界搜索不保证全局最小调整或最优。固定预约、必去与返程不会自动放松。'}

GUIDE_SCHEMA={'type':'json_schema','json_schema':{'name':'guide_places','strict':True,'schema':{
    'type':'object','properties':{'mentions':{'type':'array','items':{'type':'object','properties':{
        'name':{'type':'string'},'quote':{'type':'string'},'intent':{'type':'string','enum':['required','optional','excluded']}},
        'required':['name','quote','intent'],'additionalProperties':False}}},'required':['mentions'],'additionalProperties':False}}}

def ground_guide(text,value):
    mentions=value.get('mentions')
    if not isinstance(mentions,list) or len(mentions)>8:raise ValueError('最多提取八个地点')
    output=[];seen=set()
    for item in mentions:
        if not isinstance(item,dict) or set(item)!={'name','quote','intent'}:raise ValueError('提取字段不完整')
        name,quote,intent=item['name'],item['quote'],item['intent']
        if not isinstance(name,str) or not 2<=len(name)<=80 or not isinstance(quote,str) or not 2<=len(quote)<=250 or quote not in text or name not in quote or intent not in ('required','optional','excluded'):raise ValueError('地点和引用必须来自原文')
        if name in seen:continue
        seen.add(name);output.append({**item,'start':text.index(quote),'end':text.index(quote)+len(quote)})
    return output

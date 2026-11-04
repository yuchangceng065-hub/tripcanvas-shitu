"""Confirmed requirements and candidate-only planning; no invented POIs."""
from datetime import date,timedelta
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Requirements(BaseModel):
    model_config=ConfigDict(extra='forbid')
    city:Literal['杭州']='杭州'
    startDate:date
    days:int=Field(ge=1,le=3)
    people:int=Field(ge=1,le=10)
    mode:Literal['walk','drive']='walk'
    start:int=Field(default=570,ge=0,le=1439)
    end:int=Field(default=1110,ge=1,le=1440)
    pace:Literal['relaxed','balanced','full']='balanced'
    interests:list[str]=Field(default_factory=list,max_length=8)
    originPlaceId:str|None=None
    destinationPlaceId:str|None=None
    returnBy:int|None=Field(default=None,ge=0,le=1439)
    @model_validator(mode='after')
    def valid(self):
        if self.end<=self.start:raise ValueError('结束时间须晚于出发时间')
        if self.returnBy is not None and (not self.destinationPlaceId or not self.start<=self.returnBy<=self.end-15):raise ValueError('返程需指定地点，且时间须在当天范围内并留 15 分钟')
        if any(len(x)>40 for x in self.interests):raise ValueError('偏好文字过长')
        return self
class DraftBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(default='我的杭州旅行',min_length=1,max_length=80)
    requirements:Requirements
    candidateIds:list[str]=Field(min_length=1,max_length=18)
    useModel:bool=True
class Order(BaseModel):
    model_config=ConfigDict(extra='forbid')
    days:list[list[str]]=Field(min_length=1,max_length=3)

def check_order(order,ids,count):
    flat=[id for day in order for id in day]
    if len(order)!=count or len(flat)!=len(ids) or set(flat)!=set(ids) or any(len(day)>10 for day in order):raise ValueError('候选地点必须各使用一次，且不得引入新地点')
    return order

def heuristic(ids,pois,count):
    remaining=list(ids);ordered=[]
    while remaining:
        if not ordered:next_id=remaining[0]
        else:
            p=pois[ordered[-1]]
            next_id=min(remaining,key=lambda id:(pois[id]['lng']-p['lng'])**2+(pois[id]['lat']-p['lat'])**2)
        ordered.append(next_id);remaining.remove(next_id)
    # Geographically adjacent stops are kept on the same day.
    return [ordered[len(ordered)*i//count:len(ordered)*(i+1)//count] for i in range(count)]

def make_trip(body,order,pois):
    import uuid
    r=body.requirements;days=[]
    for i,ids in enumerate(order):
        activities=[{'id':str(uuid.uuid4()),'placeId':id,'duration':min(pois[id]['duration'],60 if r.pace=='full' else 90),'lock':'none'} for id in ids]
        if i==0 and r.originPlaceId:activities.insert(0,{'id':str(uuid.uuid4()),'placeId':r.originPlaceId,'duration':15,'lock':'keep'})
        if i==r.days-1 and r.destinationPlaceId:
            a={'id':str(uuid.uuid4()),'placeId':r.destinationPlaceId,'duration':15,'lock':'fixed' if r.returnBy is not None else 'keep'}
            if r.returnBy is not None:a['fixedStart']=r.returnBy
            activities.append(a)
        days.append({'date':str(r.startDate+timedelta(days=i)),'start':r.start,'end':r.end,'activities':activities})
    return {'id':str(uuid.uuid4()),'title':body.title,'city':r.city,'people':r.people,'mode':r.mode,'days':days,'revision':1,'requirements':r.model_dump(mode='json')}

def _shorten_options(day,before,minutes):
    """Previewable repairs: shorten unlocked stops before the conflict."""
    options=[]
    for activity in day.activities[:before]:
        if activity.lock=='none' and activity.duration-30>0:
            options.append({'action':'shorten','activityId':activity.id,'minutes':min(minutes,activity.duration-30)})
    return options

def evaluate(trip,routes,pois):
    """One authoritative hard-constraint pass. Unknown roads never count as feasible."""
    issues=[]
    for index,day in enumerate(trip.days):
        route=(routes or {}).get(index)
        legs=route.get('legs') if isinstance(route,dict) else None
        if len(day.activities)>1 and not legs:
            issues.append({'code':'route_missing','severity':'error','day':index,'activityId':None,
                'message':f'第 {index+1} 天缺少真实道路路线，无法确认可行','options':[{'action':'retry_route','day':index}]})
            continue
        end=day.start
        for position,activity in enumerate(day.activities):
            minutes=legs[position-1].get('minutes',0) if legs and position else 0
            earliest=end+minutes
            start=activity.fixedStart if activity.lock=='fixed' and activity.fixedStart is not None else earliest
            end=start+activity.duration
            if start<earliest:
                late=earliest-start
                options=_shorten_options(day,position,late)
                options.append({'action':'depart_earlier','day':index,'minutes':late})
                issues.append({'code':'fixed_too_early','severity':'error','day':index,'activityId':activity.id,
                    'message':f'第 {index+1} 天 {pois[activity.placeId]["name"]} 最早可在 {earliest//60:02d}:{earliest%60:02d} 到达，固定时间 {start//60:02d}:{start%60:02d} 早到 {late} 分钟',
                    'options':options})
            if end>day.end:
                over=end-day.end
                options=_shorten_options(day,position,over)
                options.append({'action':'extend_day','day':index,'minutes':over})
                if activity.lock=='none' and len(day.activities)>1:
                    options.append({'action':'drop','activityId':activity.id})
                issues.append({'code':'day_overflow','severity':'error','day':index,'activityId':activity.id,
                    'message':f'第 {index+1} 天超出结束时间 {over} 分钟（{pois[activity.placeId]["name"]} 结束于 {end//60:02d}:{end%60:02d}）',
                    'options':options})
        requirements=getattr(trip,'requirements',None)
        if index==len(trip.days)-1 and requirements and requirements.returnBy is not None and day.activities:
            last=day.activities[-1]
            last_start=_activity_start(day,legs,last)
            if last_start>requirements.returnBy:
                issues.append({'code':'return_deadline','severity':'error','day':index,'activityId':last.id,
                    'message':f'第 {index+1} 天最早 {last_start//60:02d}:{last_start%60:02d} 才能到达 {pois[last.placeId]["name"]}，晚于要求的 {requirements.returnBy//60:02d}:{requirements.returnBy%60:02d}',
                    'options':[{'action':'shorten','activityId':a.id,'minutes':last_start-requirements.returnBy} for a in day.activities if a.lock=='none']})
    return issues

def _activity_start(day,legs,activity):
    end=day.start
    for position,item in enumerate(day.activities):
        minutes=legs[position-1].get('minutes',0) if legs and position else 0
        earliest=end+minutes
        start=item.fixedStart if item.lock=='fixed' and item.fixedStart is not None else earliest
        end=start+item.duration
        if item.id==activity.id:return start
    return end


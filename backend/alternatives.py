"""Bounded, road-verified local search for genuinely distinct short-trip plans.

Geometry is used only to rank search candidates. All published metrics and
feasibility decisions use complete day routes (including car continuity).
"""
import asyncio,copy,math,time,uuid
from fastapi import HTTPException
from backend.planning import make_trip,evaluate,walking_km
from backend.routing import RouteBody

PROFILES=(('less-walking','少走路'),('immersive','充分游览'),('relaxed','轻松休闲'))


def recommended(p):return max(30,min(180,int(p.get('duration',60))))
def minimum(p):return max(30,math.ceil(recommended(p)*.6/15)*15)


def metrics(trip,routes):
    days=[]
    r=trip.requirements
    anchors={r.originPlaceId,r.destinationPlaceId} if r else set()
    for i,day in enumerate(trip.days):
        road=routes.get(i);legs=(road or {}).get('legs',[])
        travel=sum(leg['minutes'] for leg in legs)
        walking=walking_km(road,trip.mode) if len(day.activities)>1 else 0.0
        visits=sum(a.duration for a in day.activities if a.placeId not in anchors)
        end=day.start;waiting=0
        for j,a in enumerate(day.activities):
            earliest=end+(legs[j-1]['minutes'] if j else 0)
            start=max(earliest,a.fixedStart) if a.lock=='fixed' else earliest
            waiting+=max(0,start-earliest);end=start+a.duration
        free=waiting+max(0,day.end-end)
        days.append({'day':i,'walkingKm':round(walking,3) if walking is not None else None,
            'travelMinutes':travel,'visitMinutes':visits,'freeMinutes':free,
            'finish':end,'overMinutes':max(0,end-day.end),'waitingMinutes':waiting})
    return {'walkingKm':round(sum(d['walkingKm'] for d in days),3) if all(d['walkingKm'] is not None for d in days) else None,
        'travelMinutes':sum(d['travelMinutes'] for d in days),'visitMinutes':sum(d['visitMinutes'] for d in days),
        'freeMinutes':sum(d['freeMinutes'] for d in days),'days':days}


class SearchLimit(Exception):pass


class PlanSearch:
    def __init__(self,body,pois,sort_pois,route,validate,*,max_routes=12,max_states=24,seconds=65):
        self.body,self.pois,self.positions=body,pois,sort_pois
        self.router,self.validate=route,validate
        self.max_routes,self.max_states,self.seconds=max_routes,max_states,seconds
        self.routes={};self.queries=0;self.states=0;self.failures=[];self.stop_reason=None;self.provider_failed=False
        self.namespace=uuid.uuid4();self.trip_id=str(self.namespace)
        self.must=set(body.candidateIds)-set(body.optionalIds)
        self.optional=set(body.optionalIds)

    def distance(self,a,b):
        p,q=self.positions[a],self.positions[b]
        return math.hypot((p['lng']-q['lng'])*math.cos(math.radians(p['lat'])),p['lat']-q['lat'])*111

    def rough(self,shape,profile):
        """Ranking heuristic only, never returned as a road metric."""
        burdens=[];distance=0
        r=self.body.requirements
        for i,group in enumerate(shape):
            ids=list(group)
            if i==0 and r.originPlaceId:ids.insert(0,r.originPlaceId)
            if i==r.days-1 and r.destinationPlaceId:ids.append(r.destinationPlaceId)
            km=sum(self.distance(a,b) for a,b in zip(ids,ids[1:]));distance+=km
            minutes=sum(recommended(self.pois[id]) for id in group)+km/(4.5 if r.mode=='walk' else 23)*60
            capacity=(r.returnBy if i==r.days-1 and r.returnBy is not None else r.end)-r.start
            burdens.append((minutes,capacity))
        over=sum(max(0,minutes-capacity) for minutes,capacity in burdens)
        count=sum(map(len,shape))
        if profile=='immersive':preference=-count*25+distance*3
        elif profile=='relaxed':preference=max((minutes for minutes,_ in burdens),default=0)+count*8
        else:preference=distance*30
        return over*1000+preference

    def neighbors(self,shape):
        groups=[list(g) for g in shape];result=set()
        def add(g):
            key=tuple(tuple(day) for day in g)
            if any(g) and key!=shape:result.add(key)
        for di,group in enumerate(groups):
            for ai,id in enumerate(group):
                # Move a visit within its day or across days; anchors are separate.
                for dj in range(len(groups)):
                    for at in range(len(groups[dj])+1):
                        n=copy.deepcopy(groups);n[di].pop(ai)
                        n[dj].insert(min(at,len(n[dj])),id)
                        if len(n[dj])<=10:add(n)
                if id in self.optional:
                    n=copy.deepcopy(groups);n[di].pop(ai);add(n)
        present={id for group in groups for id in group}
        for id in self.body.candidateIds:
            if id in present:continue
            for di,group in enumerate(groups):
                if len(group)>=10:continue
                for at in range(len(group)+1):
                    n=copy.deepcopy(groups);n[di].insert(at,id);add(n)
        return result

    async def roads(self,trip):
        if self.provider_failed:raise SearchLimit(self.stop_reason or '地图服务暂不可用')
        routes={}
        for i,day in enumerate(trip.days):
            ids=tuple(a.placeId for a in day.activities)
            if len(ids)<2:continue
            if ids not in self.routes:
                if self.queries>=self.max_routes:raise SearchLimit('道路查询预算已达到上限')
                self.queries+=1
                try:
                    self.routes[ids]=await self.router(RouteBody(mode=trip.mode,placeIds=list(ids)))
                except HTTPException as error:
                    # System/provider problems stop the search; no-route candidates may be replaced.
                    if error.status_code in (429,502,503):
                        self.provider_failed=True;self.stop_reason=str(error.detail);raise SearchLimit(self.stop_reason)
                    self.routes[ids]=None
                    self.failures.append(str(error.detail))
            if self.routes[ids] is None:return None
            value=self.routes[ids]
            if value.get('expiresAt',float('inf'))<=time.time()*1000:
                raise SearchLimit('搜索期间道路估时已过期，请重新生成')
            routes[i]=value
        return routes

    def build(self,shape,profile):
        data=make_trip(self.body,shape,self.pois);data['id']=self.trip_id
        for di,day in enumerate(data['days']):
            for ai,a in enumerate(day['activities']):
                id=a['placeId'];identity=id if id in self.body.candidateIds else f'anchor:{di}:{"origin" if di==0 and ai==0 and id==self.body.requirements.originPlaceId else "destination"}:{id}'
                a['id']=str(uuid.uuid5(self.namespace,identity))
                if id in self.body.candidateIds:
                    rec=recommended(self.pois[id])
                    a['duration']=rec if profile in ('immersive','relaxed') else max(minimum(self.pois[id]),math.floor(rec*.8/15)*15)
        return self.validate(data)

    def fit(self,trip,routes,profile):
        """Shrink only within source-based dwell ranges, before moving/dropping visits."""
        for di,day in enumerate(trip.days):
            legs=routes.get(di,{}).get('legs',[])
            for _ in range(100):
                end=day.start;conflict_at=None
                for ai,a in enumerate(day.activities):
                    earliest=end+(legs[ai-1]['minutes'] if ai else 0)
                    if a.lock=='fixed' and earliest>a.fixedStart:
                        conflict_at=ai;break
                    end=max(earliest,a.fixedStart) + a.duration if a.lock=='fixed' else earliest+a.duration
                if conflict_at is None and end<=day.end:break
                choices=[a for a in day.activities[:conflict_at] if a.placeId in self.body.candidateIds and a.duration>minimum(self.pois[a.placeId])]
                if not choices:break
                target=max(choices,key=lambda a:a.duration-minimum(self.pois[a.placeId]));target.duration=max(minimum(self.pois[target.placeId]),target.duration-15)
        return trip

    def score(self,item,profile):
        m=item['metrics'];count=len(item['includedIds'])
        errors=sum(i['severity']=='error' for i in item['issues'])
        if profile=='less-walking':value=((m['walkingKm'] if m['walkingKm'] is not None else 10000),m['travelMinutes'],-count,-m['visitMinutes'])
        elif profile=='immersive':value=(-count,-m['visitMinutes'],m['travelMinutes'])
        else:value=(max((d['visitMinutes']+d['travelMinutes'] for d in m['days']),default=0),-min(d['freeMinutes'] for d in m['days']),m['travelMinutes'])
        return (errors,*value)

    async def consider(self,shape,profile):
        if self.states>=self.max_states:raise SearchLimit('候选搜索数量已达到上限')
        self.states+=1
        trip=self.build(shape,profile)
        routes=await self.roads(trip)
        if routes is None:return None
        self.fit(trip,routes,profile)
        found=evaluate(trip,routes,self.pois)
        included=[id for group in shape for id in group]
        return {'trip':trip.model_dump(mode='json',exclude_none=True),'routes':routes,'issues':found,'metrics':metrics(trip,routes),
            'includedIds':included,'omittedIds':[id for id in self.body.candidateIds if id not in included]}

    async def search(self,seed):
        seed=tuple(tuple(day) for day in seed);answers=[];closest=None
        # Interleave profiles so the first one cannot consume the whole road budget.
        trackers={profile:{'frontier':{seed},'seen':set(),'best':None,'stopped':False} for profile,_ in PROFILES}
        async def run():
            nonlocal closest
            for _ in range(8):
                for profile,label in PROFILES:
                    tracker=trackers[profile];frontier=tracker['frontier'];seen=tracker['seen']
                    if tracker['stopped'] or not frontier:continue
                    shape=min(frontier,key=lambda s:(self.rough(s,profile),s));frontier.remove(shape);seen.add(shape)
                    try:item=await self.consider(shape,profile)
                    except SearchLimit as error:
                        self.stop_reason=str(error);tracker['stopped']=True;continue
                    if item:
                        if closest is None or self.score(item,'immersive')<self.score(closest,'immersive'):closest=item
                        if tracker['best'] is None or self.score(item,profile)<self.score(tracker['best'],profile):tracker['best']=item
                    frontier.update(self.neighbors(shape)-seen)
                    tracker['frontier']=set(sorted(frontier,key=lambda s:(self.rough(s,profile),s))[:80])
        try:
            async with asyncio.timeout(self.seconds):await run()
        except TimeoutError:self.stop_reason='本轮规划时间预算已达到上限'
        for profile,label in PROFILES:
            best=trackers[profile]['best']
            if best and not any(i['severity']=='error' for i in best['issues']):answers.append({'id':profile,'label':label,**best})
        distinct=[];signatures=set();unavailable=[]
        for answer in answers:
            signature=tuple((d['date'],tuple((a['placeId'],a['duration']) for a in d['activities'])) for d in answer['trip']['days'])
            if signature in signatures:
                unavailable.append({'id':answer['id'],'reason':'与已展示方案的地点、顺序、分日和停留完全相同，已合并'})
            else:signatures.add(signature);distinct.append(answer)
        for profile,label in PROFILES:
            if not any(a['id']==profile for a in answers):unavailable.append({'id':profile,'reason':self.stop_reason or '本轮搜索未找到满足全部硬约束的不同可行方案'})
        return {'options':distinct,'diagnostic':closest if not distinct else None,'unavailable':unavailable,
            'search':{'evaluatedStates':self.states,'dayRouteQueries':self.queries,'maxDayRouteQueries':self.max_routes,
                'limited':bool(self.stop_reason),'reason':self.stop_reason,'routeFailures':list(dict.fromkeys(self.failures))[:3]},
            'metricNote':'步行仅统计道路转场与接驳，不含景点内部游览；空余为时间窗扣除活动和转场后的未分配时间，尚未自动安排用餐。',
            'warnings':['仅比较本轮有限搜索中已核验的候选，不保证全局最优。','停车入口、营业时间、预约和景点内部步行仍需确认。']}

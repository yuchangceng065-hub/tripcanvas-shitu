import asyncio,copy
import pytest
from fastapi import HTTPException
from backend.alternatives import PlanSearch,minimum
from backend.planning import AlternativesBody,evaluate
from backend.app import Trip,POIS

IDS=['quyuan','museum','art','leifeng']

def body(days=1,optional=None,end=1110,ids=None):
 ids=ids or IDS
 data=AlternativesBody(title='可行方案测试',candidateIds=ids,optionalIds=optional or [],useModel=False,
  requirements={'city':'杭州','startDate':'2026-11-14','days':days,'people':2,'mode':'walk','start':540,'end':end})
 data.requirements.mustVisitIds=[id for id in ids if id not in data.optionalIds]
 return data

def route(minutes=10,km=.6):
 calls=[]
 async def run(request):
  calls.append(tuple(request.placeIds))
  return {'source':'amap','crs':'GCJ02','legs':[{'minutes':minutes,'distance':km,'segments':[{'mode':'walk','minutes':minutes,'distance':km}]} for _ in request.placeIds[1:]]}
 return run,calls

def search(data,roads,**kwargs):
 # Use authoritative seed coordinates; geometry only ranks candidates.
 return PlanSearch(data,POIS,POIS,roads,Trip.model_validate,**kwargs)

def verify(result,data):
 assert result['search']['dayRouteQueries']<=result['search']['maxDayRouteQueries']
 assert len(result['options'])<=3
 signatures=[]
 for option in result['options']:
  trip=Trip.model_validate(option['trip'])
  assert not [i for i in evaluate(trip,option['routes'],POIS) if i['severity']=='error']
  present=[a.placeId for d in trip.days for a in d.activities]
  assert set(data.requirements.mustVisitIds)<=set(present)
  assert set(option['omittedIds'])<=set(data.optionalIds)
  assert [d.start for d in trip.days]==[data.requirements.start]*len(trip.days)
  assert [d.end for d in trip.days]==[data.requirements.end]*len(trip.days)
  for day in trip.days:
   for a in day.activities:
    if a.placeId in data.candidateIds:assert a.duration>=minimum(POIS[a.placeId])
  m=option['metrics'];assert m['travelMinutes']==sum(l['minutes'] for r in option['routes'].values() for l in r['legs'])
  assert m['walkingKm']==pytest.approx(sum(s['distance'] for r in option['routes'].values() for l in r['legs'] for s in l['segments']))
  signatures.append(str([(d['date'],[(a['placeId'],a['duration']) for a in d['activities']]) for d in option['trip']['days']]))
 assert len(set(signatures))==len(signatures)

def test_three_distinct_options_have_real_metrics_and_only_optional_omissions():
 data=body(optional=['art','leifeng']);roads,calls=route()
 result=asyncio.run(search(data,roads).search([IDS]))
 verify(result,data)
 assert len(result['options'])==3
 options={o['id']:o for o in result['options']}
 assert options['immersive']['metrics']['visitMinutes']>options['less-walking']['metrics']['visitMinutes']
 assert options['less-walking']['metrics']['walkingKm']<options['immersive']['metrics']['walkingKm']
 assert len(calls)==len(set(calls))

def test_cross_day_search_rescues_overfull_initial_distribution():
 data=body(days=2,end=720);roads,_=route()
 seed=[IDS[:3],IDS[3:]]
 engine=search(data,roads)
 first=asyncio.run(engine.consider(tuple(map(tuple,seed)),'less-walking'))
 assert any(i['severity']=='error' for i in first['issues'])
 result=asyncio.run(engine.search(seed));verify(result,data)
 assert result['options']
 assert any([len(d['activities']) for d in o['trip']['days']]==[2,2] for o in result['options'])

def test_road_time_not_geometry_decides_feasibility_and_reordering():
 data=body(ids=IDS[:3],end=780);seed=tuple(IDS[:3]);calls=[]
 async def roads(request):
  ids=tuple(request.placeIds);calls.append(ids);minutes=100 if ids==seed else 10
  return {'source':'amap','legs':[{'minutes':minutes,'distance':1,'segments':[{'mode':'walk','minutes':minutes,'distance':1}]} for _ in ids[1:]]}
 result=asyncio.run(search(data,roads).search([list(seed)]));verify(result,data)
 assert result['options']
 assert all(tuple(a['placeId'] for a in o['trip']['days'][0]['activities'])!=seed for o in result['options'])

def test_impossible_must_stops_are_not_deleted_to_claim_success():
 data=body(end=600);roads,_=route(minutes=200)
 result=asyncio.run(search(data,roads).search([IDS]));assert result['options']==[]
 assert result['diagnostic'] and any(i['severity']=='error' for i in result['diagnostic']['issues'])
 assert set(result['diagnostic']['includedIds'])==set(IDS)

def test_identical_options_are_merged_not_renamed():
 pois=copy.deepcopy(POIS);pois['quyuan']['duration']=30
 data=body(ids=['quyuan']);roads,_=route()
 result=asyncio.run(PlanSearch(data,pois,pois,roads,Trip.model_validate).search([['quyuan']]))
 assert len(result['options'])==1 and len(result['unavailable'])==2

def test_quota_failure_stops_without_fake_routes():
 data=body();calls=[]
 async def denied(request):calls.append(1);raise HTTPException(429,'地图调用预算已满')
 result=asyncio.run(search(data,denied).search([IDS]))
 assert result['options']==[] and result['search']['limited']
 # No repeated upstream requests after a global provider failure.
 assert len(calls)<=3
 assert '预算' in result['search']['reason']

def test_road_query_budget_is_enforced_even_with_many_candidates():
 data=body(optional=['art']);roads,calls=route()
 result=asyncio.run(search(data,roads,max_routes=2).search([IDS]))
 assert len(calls)==2 and result['search']['limited'];verify(result,data)

def test_return_constraint_and_stable_activity_ids_survive_all_options():
 data=body(optional=['art']);data.requirements.destinationPlaceId='station';data.requirements.returnBy=1020
 roads,_=route();result=asyncio.run(search(data,roads).search([IDS]));verify(result,data)
 stable={}
 for option in result['options']:
  day=option['trip']['days'][-1];last=day['activities'][-1]
  assert last['placeId']=='station' and last['fixedStart']==1020 and last['lock']=='fixed'
  for d in option['trip']['days']:
   for a in d['activities']:
    if a['placeId'] in stable:assert stable[a['placeId']]==a['id']
    stable[a['placeId']]=a['id']

@pytest.mark.parametrize('optional',[['unknown'],['quyuan','quyuan']])
def test_optional_ids_must_be_unique_confirmed_candidates(optional):
 with pytest.raises(ValueError):body(optional=optional)

def test_unknown_walking_with_a_budget_is_not_feasible():
 data=body(optional=['art']);data.requirements.mode='drive';data.requirements.walkBudgetKm=3
 async def unsplit(request):return {'source':'osrm','legs':[{'minutes':10,'distance':2} for _ in request.placeIds[1:]]}
 result=asyncio.run(search(data,unsplit).search([IDS]))
 assert not result['options']
 assert any(i['code']=='walk_budget_unknown' for i in result['diagnostic']['issues'])

def test_timeout_preserves_only_fully_checked_candidates():
 data=body(optional=['art']);calls=[]
 async def slow(request):
  calls.append(tuple(request.placeIds))
  if len(calls)>1:await asyncio.sleep(.05)
  return {'source':'amap','legs':[{'minutes':10,'distance':1,'segments':[{'mode':'walk','minutes':10,'distance':1}]} for _ in request.placeIds[1:]]}
 result=asyncio.run(search(data,slow,seconds=.02).search([IDS]))
 assert result['options'] and result['search']['limited'];verify(result,data)

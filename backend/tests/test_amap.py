import asyncio,copy,httpx,pytest
from fastapi import HTTPException
from backend.amap import AmapService,RoutingGateway,normalize_poi
from backend.routing import RouteBody

@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for k in ('AMAP_WEB_KEY','AMAP_JS_KEY','AMAP_JS_SECURITY_CODE'):monkeypatch.setenv(k,'test-secret')
    monkeypatch.setenv('ROUTING_PROVIDER','auto')

def fixture_pois():
    return {'a':{'name':'景点A','lng':120.14,'lat':30.26,'crs':'GCJ02'},'b':{'name':'景点B','lng':120.15,'lat':30.27,'crs':'GCJ02'},'c':{'name':'景点C','lng':120.16,'lat':30.28,'crs':'GCJ02'},
      'amap:x':{'id':'amap:x','name':'停车场X','lng':120.139,'lat':30.259,'crs':'GCJ02','kind':'停车场','provider':'amap'},
      'amap:y':{'id':'amap:y','name':'停车场Y','lng':120.159,'lat':30.279,'crs':'GCJ02','kind':'停车场','provider':'amap'}}

def transport(calls):
    def handle(r):
        calls.append(r)
        origin=r.url.params['origin'];dest=r.url.params['destination'];drive=r.url.path.endswith('driving')
        return httpx.Response(200,json={'status':'1','route':{'paths':[{'distance':'7000' if drive else '200','cost':{'duration':'600' if drive else '180'},'steps':[{'polyline':origin+';'+dest}]}]}})
    return httpx.MockTransport(handle)

def test_walking_and_driving_and_buffers_are_all_included():
    async def run():
        calls=[];pois=fixture_pois()
        async with httpx.AsyncClient(transport=transport(calls)) as client:
            service=AmapService(client,pois,lambda c:None,lambda p:p,interval=0)
            r=await service.route(RouteBody(mode='drive',placeIds=['a','b'],parkingByPlace={'a':'amap:x','b':'amap:y'}))
            leg=r['legs'][0]
            assert [s['mode'] for s in leg['segments']]==['walk','transfer','drive','transfer','walk']
            assert leg['distance']==7.4 and leg['minutes']==21
            assert r['accessConfirmed'] and r['crs']=='GCJ02'
            assert sum(s['minutes'] for s in leg['segments'] if s['estimated'])==5
            assert len(calls)==3
    asyncio.run(run())

def test_shared_parking_keeps_vehicle_and_next_leg_walks_back_to_it():
    async def run():
        calls=[];pois=fixture_pois()
        async with httpx.AsyncClient(transport=transport(calls)) as client:
            service=AmapService(client,pois,lambda c:None,lambda p:p,interval=0)
            r=await service.route(RouteBody(mode='drive',placeIds=['a','b','c'],parkingByPlace={'a':'amap:x','b':'amap:x','c':'amap:y'}))
            assert [s['mode'] for s in r['legs'][0]['segments']]==['walk']
            drives=[x for x in calls if x.url.path.endswith('driving')]
            assert len(drives)==1 and drives[0].url.params['origin']=='120.139000,30.259000'
            assert '停车场X' in r['legs'][1]['segments'][0]['label']
    asyncio.run(run())

def test_api_status_zero_is_not_success_and_is_not_cached():
    async def run():
        consumed=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'status':'0','infocode':'10003'}))) as client:
            service=AmapService(client,fixture_pois(),consumed.append,lambda p:p,interval=0)
            with pytest.raises(HTTPException) as e:await service.segment('walk',[120.14,30.26],[120.15,30.27],'步行')
            assert e.value.status_code==429 and not service.cache and consumed==['lbs']
    asyncio.run(run())

def test_provider_refusal_never_falls_back_to_another_coordinate_system():
    async def run():
        class OSM:
            async def route(self,b):raise AssertionError('must not fall back')
        class FailedAMap:
            async def route(self,b):raise HTTPException(422,'地点不可达')
        with pytest.raises(HTTPException) as e:await RoutingGateway(OSM(),FailedAMap()).route(RouteBody(mode='drive',placeIds=['a','b']))
        assert e.value.detail=='地点不可达'
    asyncio.run(run())

def test_budget_refusal_happens_before_upstream():
    async def run():
        calls=[]
        def deny(group):raise HTTPException(429,'本站预算已用完')
        async with httpx.AsyncClient(transport=transport(calls)) as client:
            service=AmapService(client,fixture_pois(),deny,lambda p:p,interval=0)
            with pytest.raises(HTTPException):await service.segment('walk',[120.14,30.26],[120.15,30.27],'步行')
            assert not calls and service.pending==0
    asyncio.run(run())

def test_gps_conversion_is_batched_with_official_separator_and_cached():
    async def run():
        calls=[];pois={'a':{'lng':120.13,'lat':30.25},'b':{'lng':120.14,'lat':30.26}}
        def handle(r):
            calls.append(r);assert r.url.params['locations']=='120.130000,30.250000|120.140000,30.260000'
            return httpx.Response(200,json={'status':'1','locations':'120.135,30.255;120.145,30.265'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service=AmapService(client,pois,lambda c:None,lambda p:p,interval=0)
            assert (await service.coordinates(['a','b']))['a']==[120.135,30.255]
            await service.coordinates(['a']);assert len(calls)==1
    asyncio.run(run())

def test_amap_poi_uses_explicit_gcj02_and_navigation_entrance():
    p=normalize_poi({'id':'B001','name':'测试停车场','location':'120.14,30.26','typecode':'150900','cityname':'杭州市','navi':{'entr_location':'120.141,30.261'}})
    assert p['crs']=='GCJ02' and p['kind']=='停车场' and p['entrance']==[120.141,30.261]

def test_internal_parking_is_not_automatically_selected():
    async def run():
        pois=fixture_pois();internal={**pois['amap:x'],'id':'amap:internal','name':'浙江工业大学内部停车场'};public=pois['amap:y'];calls=[]
        async with httpx.AsyncClient(transport=transport(calls)) as client:
            service=AmapService(client,pois,lambda c:None,lambda p:p,interval=0)
            async def parking(id):return [internal,public]
            service.parking=parking
            r=await service.route(RouteBody(mode='drive',placeIds=['a','b']))
            assert all(a['parking']['id']=='amap:y' for a in r['accessPoints'])
            assert all(s['mode']=='walk' for s in r['legs'][0]['segments'])
            async def internal_only(id):return [internal]
            service.parking=internal_only
            with pytest.raises(HTTPException) as e:await service.route(RouteBody(mode='drive',placeIds=['a','b']))
            assert e.value.status_code==422 and '通行' in e.value.detail
    asyncio.run(run())

def test_changing_parking_moves_the_vehicle_and_keeps_it_for_shared_stops():
    async def run():
        calls=[];pois=fixture_pois()
        async with httpx.AsyncClient(transport=transport(calls)) as client:
            service=AmapService(client,pois,lambda c:None,lambda p:p,interval=0)
            r=await service.route(RouteBody(mode='drive',placeIds=['a','b','c'],
                parkingByPlace={'a':'amap:x','b':'amap:y','c':'amap:y'}))
            first,second=r['legs'][0]['segments'],r['legs'][1]['segments']
            assert [s['mode'] for s in first]==['walk','transfer','drive','transfer','walk']
            assert '停车场Y' in first[2]['label']
            assert [s['mode'] for s in second]==['walk']
            assert '车辆仍停在' in second[0]['label'] and '停车场Y' in second[0]['label']
            drives=[call for call in calls if call.url.path.endswith('driving')]
            assert len(drives)==1
            assert r['accessPoints'][1]['parking']['id']=='amap:y' and r['accessConfirmed']
    asyncio.run(run())

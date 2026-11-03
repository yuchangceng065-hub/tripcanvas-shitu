import asyncio
import time
import httpx
import pytest
from fastapi import HTTPException
from backend.routing import Router, RouteBody, normalize

POIS={'a':{'lng':120.12,'lat':30.25},'b':{'lng':120.14,'lat':30.26}}
BODY=RouteBody(mode='walk',placeIds=['a','b'])

def response():
    return {'code':'Ok','routes':[{'legs':[{'distance':1500,'duration':901}],
            'geometry':{'type':'LineString','coordinates':[[120.12,30.25],[120.14,30.26]]}}],
            'waypoints':[{'location':[120.12,30.25],'distance':5},{'location':[120.14,30.26],'distance':10}]}

def test_units_and_missing_leg():
    assert normalize(response(),BODY)['legs']==[{'distance':1.5,'minutes':16}]
    data=response();data['routes'][0]['legs']=[]
    with pytest.raises(ValueError):normalize(data,BODY)
    data=response();data['routes'][0]['legs'][0]['duration']=float('nan')
    with pytest.raises(ValueError):normalize(data,BODY)

def test_coalescing_cache_mode_and_spacing():
    async def run():
        calls=[]
        def handle(request):
            calls.append((time.monotonic(),str(request.url)))
            return httpx.Response(200,json=response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            router=Router(client,POIS,interval=.025)
            a,b=await asyncio.gather(router.route(BODY),router.route(BODY))
            assert len(calls)==1 and b['cached'] and not a['cached']
            await router.route(RouteBody(mode='drive',placeIds=['a','b']))
            assert len(calls)==2 and calls[1][0]-calls[0][0]>=.024
            assert 'routed-foot' in calls[0][1] and 'routed-car' in calls[1][1]
    asyncio.run(run())

def test_no_route_and_unknown_place_never_fabricate_results():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'code':'NoSegment'}))) as client:
            router=Router(client,POIS,interval=0)
            with pytest.raises(HTTPException) as error:await router.route(BODY)
            assert error.value.status_code==422 and not router.cache
            with pytest.raises(HTTPException) as error:await router.route(RouteBody(mode='walk',placeIds=['a','invented']))
            assert error.value.status_code==422
    asyncio.run(run())

def test_timeout_is_recoverable_and_releases_queue():
    async def run():
        def fail(request):raise httpx.ReadTimeout('test timeout')
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            router=Router(client,POIS,interval=0)
            with pytest.raises(HTTPException) as error:await router.route(BODY)
            assert error.value.status_code==503 and router.queued==0 and not router.cache
    asyncio.run(run())

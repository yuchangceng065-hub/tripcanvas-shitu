import os
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from sqlalchemy.orm import Session
from backend.db import User
from backend.app import (ORIGINS,amap_enabled,client_ip,db,optional_user,remember_place,
    reserve_amap_usage,reserve_anonymous,throttle)
from backend.places import POIS
from backend.routing import RouteBody
from backend.schemas import CoordinatesBody
router=APIRouter()

@router.post('/api/v1/routes')
async def roads(data:RouteBody,request:Request,user:User|None=Depends(optional_user)):
    if user:throttle('route:'+user.id,30)
    else:
        throttle('route:'+client_ip(request),10)
        reserve_anonymous('route',40)
    return await request.app.state.router.route(data)

@router.get('/api/v1/places/search')
async def search_places(request:Request,q:str='',s:Session=Depends(db),user:User|None=Depends(optional_user)):
    q=q.strip()
    if not 2<=len(q)<=100:raise HTTPException(422,'请输入 2—100 个字符的地点名称')
    if user:throttle('search:'+user.id,20)
    else:
        throttle('search:'+client_ip(request),8)
        reserve_anonymous('search',80)
    source='amap' if amap_enabled() else 'photon'
    found=await (request.app.state.amap.search(q) if amap_enabled() else request.app.state.search.search(q))
    found=[remember_place(p) for p in found]
    return {'source':source,'places':found,'attribution':'高德地图' if source=='amap' else '© OpenStreetMap contributors / Photon'}


@router.get('/api/v1/places/{id}/parking')
async def search_parking(id:str,request:Request,user:User|None=Depends(optional_user)):
    if id not in POIS:raise HTTPException(404,'地点不存在')
    if user:throttle('parking:'+user.id,30)
    else:
        throttle('parking:'+client_ip(request),10)
        reserve_anonymous('parking',60)
    return {'places':await request.app.state.amap.parking(id),'source':'amap'}


@router.post('/api/v1/map/coordinates')
async def map_coordinates(data:CoordinatesBody,request:Request,user:User|None=Depends(optional_user)):
    if user:throttle('coordinates:'+user.id,30)
    else:
        throttle('coordinates:'+client_ip(request),10)
        reserve_anonymous('coordinates',60)
    return {'crs':'GCJ02','locations':await request.app.state.amap.coordinates(data.placeIds)}


@router.get('/api/v1/map/config')
def map_config(request:Request,user:User|None=Depends(optional_user)):
    if not amap_enabled():return {'provider':'osm','ready':False}
    if user:throttle('map:'+user.id,30)
    else:
        throttle('map:'+client_ip(request),10)
        reserve_anonymous('map',200)
    reserve_amap_usage('map')
    # Only the domain-restricted public JS key is returned. The security code
    # and Web-service key remain on the server.
    origin=os.getenv('PUBLIC_API_ORIGIN') or os.getenv('RENDER_EXTERNAL_URL') or str(request.base_url)
    return {'provider':'amap','ready':True,'jsKey':os.environ['AMAP_JS_KEY'],'serviceHost':origin.rstrip('/')+'/_AMapService'}


@router.get('/_AMapService/{path:path}')
async def map_security_proxy(path:str,request:Request):
    if not amap_enabled() or path!='v4/map/styles':raise HTTPException(404,'地图资源不存在')
    origin=request.headers.get('origin')
    if not origin:
        ref=urlsplit(request.headers.get('referer',''));origin=ref.scheme+'://'+ref.netloc
    if origin not in ORIGINS:raise HTTPException(403,'地图请求来源不受信任')
    params=dict(request.query_params)
    if params.get('key')!=os.environ['AMAP_JS_KEY']:raise HTTPException(403,'地图 Key 不匹配')
    throttle('map-proxy:'+client_ip(request),60)
    params['jscode']=os.environ['AMAP_JS_SECURITY_CODE']
    try:
        upstream=await request.app.state.http.get('https://webapi.amap.com/v4/map/styles',params=params,headers={'Referer':origin+'/','Origin':origin})
        if upstream.is_redirect:raise HTTPException(502,'地图资源发生重定向，未转发密钥')
        return Response(content=upstream.content,status_code=upstream.status_code,media_type=upstream.headers.get('content-type','application/json'),headers={'Cache-Control':'private, max-age=300'})
    except httpx.HTTPError:raise HTTPException(503,'地图资源暂不可用')


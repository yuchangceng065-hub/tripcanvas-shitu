"""AMap POIs and walking/self-drive access chains. Coordinates stay GCJ-02.

Only fixed upstream endpoints are used. Provider refusals are never treated as
successful routes. A persistent budget callback runs before each paid-capable
request; one worker serializes requests and bounds cache/queue sizes.
"""
from __future__ import annotations
import asyncio,copy,math,os,re,time
from collections import OrderedDict
import httpx
from fastapi import HTTPException
from backend.routing import RouteBody


def enabled():
    return os.getenv('ROUTING_PROVIDER','auto')!='osrm' and all(os.getenv(k) for k in ('AMAP_WEB_KEY','AMAP_JS_KEY','AMAP_JS_SECURITY_CODE'))


def point(value):
    try:
        pair=[float(v) for v in value.split(',')] if isinstance(value,str) else list(value)
        if len(pair)!=2 or not all(math.isfinite(v) for v in pair) or not 119<=pair[0]<=122 or not 29<=pair[1]<=32:raise ValueError()
        return pair
    except (ValueError,TypeError):raise ValueError('高德返回的坐标无效')


def coord_text(p):return ','.join(f'{v:.6f}' for v in p)


def restricted_parking(p):
    name=str(p.get('name',''));address=str(p.get('address',''))
    return bool(re.search(r'内部|专用|职工|员工|教工|校内|业主|月租|小区|公寓|大学.*停车|学院.*停车',name) or re.search(r'校内|寺内|院内',address))


def normalize_poi(p):
    location=point(p['location']);name=str(p['name']).strip();identity=str(p['id'])
    if not name or not identity or len(identity)>80:raise ValueError('无效地点')
    code=str(p.get('typecode',''));kind='停车场' if code.startswith('1509') else str(p.get('type','地点')).split(';')[-1]
    navi=p.get('navi') if isinstance(p.get('navi'),dict) else {}
    result={'id':'amap:'+identity,'amapId':identity,'name':name[:150],'kind':kind,'tag':'高德搜索地点',
        'description':'高德地点数据；入口开放、预约和费用请出行前确认。','duration':60,'cost':0,'costKnown':False,
        'lng':location[0],'lat':location[1],'x':0,'y':0,'indoor':any(x in kind for x in ('馆','餐','咖啡','图书')),
        'image':'museum' if '馆' in kind else 'lake','crs':'GCJ02','provider':'amap',
        'coordinateSource':'https://uri.amap.com/marker?position='+coord_text(location),
        'address':' · '.join(str(p[k]) for k in ('cityname','adname','address') if isinstance(p.get(k),str) and p[k])[:500] or '请核对地图位置',
        'retrievedAt':int(time.time()*1000)}
    if kind=='停车场':result['restrictedAccess']=restricted_parking(result)
    for source,target in [('entr_location','entrance'),('exit_location','exit')]:
        value=navi.get(source) or p.get(source)
        if value:
            try:result[target]=point(value)
            except ValueError:pass
    return result


class AmapService:
    def __init__(self,client,pois,consume,remember,interval=.4):
        self.client,self.pois,self.consume,self.remember=client,pois,consume,remember
        self.interval=interval;self.lock=asyncio.Lock();self.route_lock=asyncio.Lock();self.cache=OrderedDict();self.converted={};self.next_at=0;self.pending=0;self.route_queued=0

    async def request(self,path,params,category='lbs',ttl=300):
        allowed={'/v5/direction/walking','/v5/direction/driving','/v5/place/text','/v5/place/around','/v3/assistant/coordinate/convert'}
        if path not in allowed:raise HTTPException(400,'不支持的地图请求')
        if not enabled():raise HTTPException(503,'高德服务尚未配置完整')
        cache_key=(path,tuple(sorted((k,str(v)) for k,v in params.items())))
        if self.pending>=12:raise HTTPException(503,'地图服务繁忙，请稍后重试')
        self.pending+=1
        try:
            async with self.lock:
                cached=self.cache.get(cache_key)
                if cached and time.time()-cached[0]<ttl:return copy.deepcopy(cached[1])
                await asyncio.sleep(max(0,self.next_at-time.monotonic()));self.next_at=time.monotonic()+self.interval
                self.consume(category)
                try:
                    response=await self.client.get('https://restapi.amap.com'+path,params={**params,'key':os.environ['AMAP_WEB_KEY']})
                    response.raise_for_status();data=response.json()
                except (httpx.HTTPError,ValueError):raise HTTPException(503,'高德服务暂不可用，未切换为其他数据源，请稍后重试')
                if str(data.get('status'))!='1':
                    code=str(data.get('infocode',''))
                    if code in ('10003','10004','10019','10020','10021','10044'):raise HTTPException(429,'高德调用额度或频率受限，请稍后重试')
                    if code in ('10001','10005','10006','10007','10008','10009','10012','10013','10014'):raise HTTPException(503,'高德密钥或访问权限配置需要检查')
                    raise HTTPException(422,'高德未返回可用结果，请确认地点或停车点；错误码 '+code[:12])
                data['_tripcanvasFetchedAt']=int(time.time()*1000)
                self.cache[cache_key]=(time.time(),data);self.cache.move_to_end(cache_key)
                while len(self.cache)>512:self.cache.popitem(last=False)
                return copy.deepcopy(data)
        finally:self.pending-=1

    async def coordinates(self,ids):
        if any(id not in self.pois for id in ids):raise HTTPException(422,'包含未知地点')
        result={};missing=[]
        for id in dict.fromkeys(ids):
            p=self.pois[id]
            if p.get('crs')=='GCJ02':result[id]=point([p['lng'],p['lat']])
            else:
                key=(p['lng'],p['lat'])
                if key in self.converted:result[id]=self.converted[key]
                else:missing.append(id)
        if missing:
            data=await self.request('/v3/assistant/coordinate/convert',{'locations':'|'.join(coord_text([self.pois[id]['lng'],self.pois[id]['lat']]) for id in missing),'coordsys':'gps'},ttl=86400)
            converted=data.get('locations','').split(';')
            if len(converted)!=len(missing):raise HTTPException(502,'高德坐标转换结果不完整')
            try:
                for id,raw in zip(missing,converted):
                    value=point(raw);p=self.pois[id];self.converted[(p['lng'],p['lat'])]=value;result[id]=value
            except ValueError:raise HTTPException(502,'高德坐标转换结果无效')
        return result

    def save_pois(self,data,parking=False):
        output=[]
        for raw in data.get('pois',[]):
            try:
                p=normalize_poi(raw)
                if parking and p['kind']!='停车场':continue
                # Limit the product to Hangzhou; do not silently return nearby cities.
                if raw.get('cityname') and '杭州' not in str(raw['cityname']):continue
                saved=self.remember(p)
                if not any(x['id']==saved['id'] for x in output):output.append(saved)
            except (ValueError,KeyError,TypeError):continue
        return output

    async def search(self,q):
        data=await self.request('/v5/place/text',{'keywords':q,'region':'330100','city_limit':'true','page_size':8,'show_fields':'navi'},'search',ttl=3600)
        return self.save_pois(data)

    async def parking(self,id):
        position=(await self.coordinates([id]))[id]
        data=await self.request('/v5/place/around',{'location':coord_text(position),'types':'150900','radius':2000,'sortrule':'distance','page_size':20,'show_fields':'navi'},'search',ttl=3600)
        return self.save_pois(data,parking=True)

    async def segment(self,mode,origin,destination,label):
        if origin==destination:return None
        data=await self.request('/v5/direction/'+('walking' if mode=='walk' else 'driving'),{'origin':coord_text(origin),'destination':coord_text(destination),'show_fields':'cost,polyline'+(',tmcs' if mode=='drive' else ''),**({'strategy':32} if mode=='drive' else {})},ttl=300 if mode=='drive' else 86400)
        if not data.get('route',{}).get('paths'):raise HTTPException(422,'该路段未找到可行的'+('步行' if mode=='walk' else '驾车')+'路线，请调整接驳点')
        try:
            path=data['route']['paths'][0];distance=float(path['distance'])/1000;duration=float(path['cost']['duration'])
            if not all(math.isfinite(x) and x>=0 for x in (distance,duration)):raise ValueError()
            coords=[];traffic=[]
            for step in path['steps']:
                for raw in str(step.get('polyline','')).split(';'):
                    if not raw:continue
                    p=point(raw)
                    if not coords or p!=coords[-1]:coords.append(p)
                for tmc in step.get('tmcs',[]) if isinstance(step.get('tmcs'),list) else []:
                    status=tmc.get('tmc_status')
                    if status and status not in traffic:traffic.append(status)
            if len(coords)<2:raise ValueError()
        except (KeyError,IndexError,TypeError,ValueError):raise HTTPException(502,'高德返回的路线或用时不完整，未生成估算替代')
        return {'mode':mode,'label':label,'distance':round(distance,4),'minutes':math.ceil(duration/60),
            'geometry':{'type':'LineString','coordinates':coords},'traffic':traffic,'estimated':False,
            'expiresAt':data.get('_tripcanvasFetchedAt',int(time.time()*1000))+(300000 if mode=='drive' else 86400000)}

    async def route(self,body:RouteBody):
        if self.route_queued>=5:raise HTTPException(503,'混合路线正在计算，请稍后重试')
        self.route_queued+=1
        try:
            async with self.route_lock:return await self._route(body)
        finally:self.route_queued-=1

    async def _route(self,body):
        positions=await self.coordinates(body.placeIds)
        access=[];parkings={};warnings=[]
        if body.mode=='drive':warnings.append('按车辆初始停在首站所选停车点计算；景区内部可达性与停车点开放仍需确认。')
        if body.mode=='drive':
            for id in dict.fromkeys(body.placeIds):
                selected=body.parkingByPlace.get(id)
                if selected:
                    park=self.pois.get(selected)
                    if not park or park.get('provider')!='amap' or park.get('kind')!='停车场':raise HTTPException(422,'停车点已失效，请重新搜索并确认')
                    dx=(park['lng']-positions[id][0])*math.cos(math.radians(positions[id][1]))*111320
                    dy=(park['lat']-positions[id][1])*111320
                    if math.hypot(dx,dy)>3000:raise HTTPException(422,'停车点距离该地点过远，请选择附近停车点')
                else:
                    nearby=await self.parking(id)
                    if not nearby:raise HTTPException(422,self.pois[id]['name']+'附近未找到停车点，请在交通与停车设置中核对，或改用步行')
                    park=next((p for p in nearby if not restricted_parking(p)),None)
                    if park is None:raise HTTPException(422,self.pois[id]['name']+'附近目前仅找到可能限制通行的停车点，请在交通与停车中手动核对资格，或改用步行')
                parkings[id]=park
                access.append({'placeId':id,'parking':park,'confirmed':bool(selected)})
                if restricted_parking(park):warnings.append(park['name']+'可能限制外来车辆，请确认通行资格。')
            if any(not a['confirmed'] for a in access):warnings.append('部分停车点由附近搜索推荐，尚未确认车位、开放时间和入口，请在交通与停车中确认。')
        segments_by_leg=[];geometry=[];vehicle=parkings.get(body.placeIds[0])
        for origin_id,dest_id in zip(body.placeIds,body.placeIds[1:]):
            a,b=positions[origin_id],positions[dest_id];segments=[]
            if body.mode=='walk':
                s=await self.segment('walk',a,b,'步行到下一站')
                if s:segments.append(s)
            else:
                target=parkings[dest_id]
                if vehicle['id']==target['id']:
                    s=await self.segment('walk',a,b,'步行游览，车辆仍停在 '+vehicle['name'])
                    if s:segments.append(s)
                else:
                    car_at=point(vehicle.get('entrance') or [vehicle['lng'],vehicle['lat']]);car_to=point(target.get('entrance') or [target['lng'],target['lat']])
                    for stage,(mode,start,end,label) in enumerate([('walk',a,car_at,'步行回 '+vehicle['name']),('drive',car_at,car_to,'驾车至 '+target['name']),('walk',car_to,b,'步行到 '+self.pois[dest_id]['name'])]):
                        s=await self.segment(mode,start,end,label)
                        if s:segments.append(s)
                        if stage==0 and body.parkingMinutes//2:
                            segments.append({'mode':'transfer','label':'取车预留','distance':0,'minutes':body.parkingMinutes//2,'estimated':True})
                        if mode=='drive' and body.parkingMinutes-body.parkingMinutes//2:
                            segments.append({'mode':'transfer','label':'停车预留','distance':0,'minutes':body.parkingMinutes-body.parkingMinutes//2,'estimated':True})
                    vehicle=target
            for s in segments:
                for p in s.get('geometry',{}).get('coordinates',[]):
                    if not geometry or geometry[-1]!=p:geometry.append(p)
            segments_by_leg.append({'distance':round(sum(s['distance'] for s in segments),4),'minutes':sum(s['minutes'] for s in segments),'segments':segments})
        if len(geometry)<2:
            # Identical consecutive points are a zero-length route, not a made-up road.
            geometry=[positions[body.placeIds[0]],positions[body.placeIds[-1]]]
        now=int(time.time()*1000)
        return {'mode':body.mode,'placeIds':body.placeIds,'source':'amap','crs':'GCJ02','legs':segments_by_leg,
            'geometry':{'type':'LineString','coordinates':geometry},'waypoints':[{'location':positions[id],'distance':0} for id in body.placeIds],
            'locations':positions,'accessPoints':access,'accessConfirmed':all(x['confirmed'] for x in access),
            'parkingByPlace':body.parkingByPlace,'parkingMinutes':body.parkingMinutes,'warnings':warnings,
            'fetchedAt':now,'expiresAt':min([s['expiresAt'] for leg in segments_by_leg for s in leg['segments'] if 'expiresAt' in s]+[now+(300000 if body.mode=='drive' else 86400000)]),'cached':False}


class RoutingGateway:
    def __init__(self,osm,amap):self.osm,self.amap=osm,amap
    async def route(self,body):
        if enabled():return await self.amap.route(body)
        return await self.osm.route(body)

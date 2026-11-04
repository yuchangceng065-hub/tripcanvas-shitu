"""Submit-only Photon search. One global request/second, bounded cache, WGS84."""
import asyncio,math,os,time
from collections import OrderedDict
from fastapi import HTTPException
import httpx

BBOX=(119.8,29.8,120.7,30.7)
def normalize(feature):
    p=feature['properties'];lng,lat=feature['geometry']['coordinates']
    if feature['geometry']['type']!='Point' or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (lng,lat)):raise ValueError('Invalid coordinates')
    if not (BBOX[0]<=lng<=BBOX[2] and BBOX[1]<=lat<=BBOX[3]):raise ValueError('Outside Hangzhou coverage')
    kind={'N':'node','W':'way','R':'relation'}.get(p['osm_type'])
    if not kind or not isinstance(p['osm_id'],int) or not p.get('name'):raise ValueError('Missing OSM identity')
    category=p.get('osm_value','地点');indoor=category in ('museum','gallery','restaurant','cafe','library')
    address=' · '.join(dict.fromkeys(str(p[k]) for k in ('state','city','district','street','housenumber') if p.get(k)))
    return {'id':f"osm:{kind}:{p['osm_id']}",'name':p['name'][:150],'address':address[:500] or '数据源未提供完整地址，请在地图核实',
      'kind':{'museum':'博物馆','gallery':'美术馆','restaurant':'餐厅','cafe':'咖啡','park':'公园','station':'车站'}.get(category,'地点'),
      'tag':'搜索确认地点','description':'来自 OpenStreetMap 的地点，入口、开放和费用请出行前确认。','duration':60,'cost':0,'costKnown':False,
      'lng':lng,'lat':lat,'x':0,'y':0,'indoor':indoor,'image':'museum' if indoor else 'lake','crs':'WGS84','provider':'photon',
      'coordinateSource':f"https://www.openstreetmap.org/{kind}/{p['osm_id']}",'retrievedAt':int(time.time()*1000)}

class PlaceSearch:
    def __init__(self,client):self.client=client;self.lock=asyncio.Lock();self.cache=OrderedDict();self.next_at=0;self.pending=0
    async def search(self,q):
        if self.pending>=5:raise HTTPException(503,'地点搜索繁忙，请稍后重试')
        self.pending+=1
        try:
            async with self.lock:
                cached=self.cache.get(q)
                if cached and time.time()-cached[0]<86400:return cached[1]
                await asyncio.sleep(max(0,self.next_at-time.monotonic()));self.next_at=time.monotonic()+1.1
                try:
                    response=await self.client.get(os.getenv('PHOTON_URL','https://photon.komoot.io').rstrip('/')+'/api/',params={'q':q,'bbox':','.join(map(str,BBOX)),'limit':8})
                    response.raise_for_status();data=response.json()
                    results=[]
                    for feature in data['features']:
                        try:
                            place=normalize(feature)
                            if not any(p['id']==place['id'] for p in results):results.append(place)
                        except (KeyError,ValueError,TypeError):continue
                except (httpx.HTTPError,ValueError,KeyError,TypeError):raise HTTPException(503,'外部地点搜索暂不可用，可稍后重试；已选地点仍保留')
                self.cache[q]=(time.time(),results)
                while len(self.cache)>128:self.cache.popitem(last=False)
                return results
        finally:self.pending-=1

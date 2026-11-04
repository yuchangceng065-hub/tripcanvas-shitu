import { describe,it,expect,vi,afterEach } from 'vitest'
import { createRenderer,ref,nextTick } from 'vue'
import { createTrip,clone,schedule,validate } from './domain'
import { matchesRoute,routeKey,normalizeOSRM,useRoadRoutes,validRoute,walkingKm,freshness,type RoadRoute } from './routes'
import { api } from './api'
vi.mock('./api',()=>({connection:{routing:'osrm',map:'osm'},apiAvailable:true,backendConfigured:true,checkAPI:vi.fn(async()=>({status:'ok'})),api:vi.fn()}))
function road(ids:string[],minutes=20):RoadRoute{return {mode:'walk',placeIds:ids,source:'osrm',legs:ids.slice(1).map(()=>({minutes,distance:1.5})),geometry:{type:'LineString',coordinates:[[120.13,30.25],[120.14,30.26]]},waypoints:ids.map(()=>({location:[120.13,30.25],distance:0})),fetchedAt:Date.now(),cached:false}}
afterEach(()=>{vi.useRealTimers();vi.clearAllMocks()})
describe('真实道路排程',()=>{
 it('道路用时替换估算且仍保留固定午餐',()=>{const t=createTrip();const r=road(t.days[0]!.activities.map(a=>a.placeId),70);const entries=schedule(t,0,r);expect(entries[1]!.travel).toBe(70);expect(entries[2]!.start).toBe(890);expect(entries[2]!.conflict).toBe(true);expect(validate(t,{[routeKey(t,0)]:r}).some(e=>e.day===0)).toBe(true)})
 it('顺序或方式变化时不应用旧路线',()=>{const t=createTrip(),r=road(t.days[0]!.activities.map(a=>a.placeId));const changed=clone(t);changed.days[0]!.activities.reverse();expect(matchesRoute(changed,0,r)).toBe(false);changed.mode='drive';expect(matchesRoute(changed,0,r)).toBe(false)})
 it('拒绝错误路段数、负值和无路线响应',()=>{expect(()=>normalizeOSRM({code:'NoRoute'},'walk',['a','b'])).toThrow('暂无');expect(()=>normalizeOSRM({code:'Ok',routes:[{legs:[{distance:-1,duration:1}],geometry:{type:'LineString',coordinates:[[1,2],[3,4]]}}],waypoints:[]},'walk',['a','b'])).toThrow('不完整')})
 it('切换日期后的旧请求不会覆盖当前路线',async()=>{
  vi.useFakeTimers()
  const t=ref(createTrip()),day=ref(0);let first!:(r:RoadRoute)=>void,second!:(r:RoadRoute)=>void
  vi.mocked(api).mockImplementationOnce(()=>new Promise(r=>first=r)).mockImplementationOnce(()=>new Promise(r=>second=r))
  const renderer=createRenderer<any,any>({createElement:()=>({}),createText:()=>({}),createComment:()=>({}),insert:()=>{},remove:()=>{},setText:()=>{},setElementText:()=>{},parentNode:()=>null,nextSibling:()=>null,patchProp:()=>{}})
  let state!:ReturnType<typeof useRoadRoutes>
  const app=renderer.createApp({setup(){state=useRoadRoutes(t,day);return()=>null}});app.mount({})
  await vi.advanceTimersByTimeAsync(500);day.value=1;await nextTick();await vi.advanceTimersByTimeAsync(500)
  second(road(t.value.days[1]!.activities.map(a=>a.placeId)));await vi.advanceTimersByTimeAsync(0)
  const expected=state.state.value.route
  first(road(t.value.days[0]!.activities.map(a=>a.placeId)));await vi.advanceTimersByTimeAsync(0)
  expect(state.state.value.status).toBe('ready');expect(state.state.value.route).toEqual(expected);expect(expected?.placeIds[0]).toBe('liulang');app.unmount()
 })
})

describe('高德混合交通与停车条件',()=>{
 function mixed(t:ReturnType<typeof createTrip>):RoadRoute{
  t.mode='drive';t.days[0]!.activities=t.days[0]!.activities.slice(0,2)
  const r=road(t.days[0]!.activities.map(a=>a.placeId));r.mode='drive';r.source='amap';r.crs='GCJ02';r.locations=Object.fromEntries(r.placeIds.map(id=>[id,[120.14,30.26]]));r.accessConfirmed=false;r.parkingByPlace={};r.parkingMinutes=5
  const park={id:'amap:parking-test',name:'测试停车场',provider:'amap',crs:'GCJ02',lng:120.14,lat:30.26} as any
  r.accessPoints=r.placeIds.map(id=>({placeId:id,parking:park,confirmed:false}))
  r.legs=[{distance:7.4,minutes:21,segments:[{mode:'walk',label:'回停车点',distance:.2,minutes:3,geometry:r.geometry,estimated:false},{mode:'drive',label:'驾车',distance:7,minutes:10,geometry:r.geometry,estimated:false},{mode:'walk',label:'进入景区',distance:.2,minutes:3,geometry:r.geometry,estimated:false},{mode:'transfer',label:'停车预留',distance:0,minutes:5,estimated:true}]}]
  return r
 }
 it('完整转场用时进入时间轴，不能只计算驾车的10分钟',()=>{const t=createTrip(),r=mixed(t);expect(schedule(t,0,r)[1]!.travel).toBe(21)})
 it('拒绝缺少步行路线、分段合计错误和坐标系缺失的结果',()=>{const t=createTrip(),r=mixed(t);expect(validRoute(r,'drive',r.placeIds)).toBe(true);r.legs[0]!.minutes=10;expect(validRoute(r,'drive',r.placeIds)).toBe(false);r.legs[0]!.minutes=21;delete r.crs;expect(validRoute(r,'drive',r.placeIds)).toBe(false)})
 it('改变停车点或停车预留后，旧路线失效并生成不同缓存键',()=>{const t=createTrip(),r=mixed(t),key=routeKey(t,0);t.mobility={parkingByPlace:{quyuan:'amap:another-parking'},parkingMinutes:10};expect(routeKey(t,0)).not.toBe(key);expect(matchesRoute(t,0,r)).toBe(false)})
})

describe('步行距离统计',()=>{
 it('步行模式累加路段，驾车只统计步行接驳',()=>{
  const t=createTrip()
  const walk=walkingKm(road(t.days[0]!.activities.map(a=>a.placeId)),'walk')
  expect(walk).toBeCloseTo(6)
  const mixed:RoadRoute={...road(['a','b']),source:'amap',mode:'drive',legs:[{distance:2.4,minutes:12,segments:[
   {mode:'walk',label:'步行回停车点',distance:0.6,minutes:8,estimated:false},
   {mode:'drive',label:'驾车',distance:1.5,minutes:4,estimated:false},
   {mode:'walk',label:'步行到景点',distance:0.3,minutes:4,estimated:false}]}]}
  expect(walkingKm(mixed,'drive')).toBeCloseTo(0.9)
  expect(walkingKm(undefined,'walk')).toBeNull()
  const drive:RoadRoute={...road(['a','b']),mode:'drive'}
  expect(walkingKm(drive,'drive')).toBeNull()
 })
})

describe('路线新鲜度',()=>{
 it('驾车估时到期后明确标记过期',()=>{
  const now=Date.parse('2026-11-04T12:10:00+08:00')
  const route:RoadRoute={...road(['a','b']),source:'amap',mode:'drive',fetchedAt:Date.parse('2026-11-04T12:00:00+08:00'),expiresAt:Date.parse('2026-11-04T12:05:00+08:00')}
  const stale=freshness(route,now)
  expect(stale.expired).toBe(true)
  expect(stale.label).toContain('已过期')
  const fresh= freshness(route,Date.parse('2026-11-04T12:02:30+08:00'))
  expect(fresh.expired).toBe(false)
  expect(fresh.minutes).toBe(3)
 })
})

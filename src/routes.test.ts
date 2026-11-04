import { describe,it,expect,vi,afterEach } from 'vitest'
import { createRenderer,ref,nextTick } from 'vue'
import { createTrip,clone,schedule,validate } from './domain'
import { matchesRoute,routeKey,normalizeOSRM,useRoadRoutes,type RoadRoute } from './routes'
import { api } from './api'
vi.mock('./api',()=>({apiAvailable:true,backendConfigured:true,checkAPI:vi.fn(async()=>({status:'ok'})),api:vi.fn()}))
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

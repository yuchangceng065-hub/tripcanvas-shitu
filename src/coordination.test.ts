import {describe,it,expect,vi} from 'vitest'
import {createTrip,clone,suggest,departureProposal,proposalErrors,lockedChanges,schedule,type Trip,type Proposal} from './domain'
import {routeKey,type RoadRoute} from './routes'
import {coordinateProposal,coordinationViolations,earlierProposal,fitDurations} from './coordination'
function road(t:Trip,day:number,minutes:number|number[]):RoadRoute {
 const ids=t.days[day]!.activities.map(a=>a.placeId)
 return {mode:t.mode,placeIds:ids,source:'osrm',legs:ids.slice(1).map((_,i)=>({minutes:Array.isArray(minutes)?minutes[i]!:minutes,distance:1})),geometry:{type:'LineString',coordinates:[[120.13,30.25],[120.15,30.26]]},waypoints:ids.map(()=>({location:[120.13,30.25],distance:0})),fetchedAt:Date.now(),cached:false}
}
function candidate(t:Trip):Proposal{return {id:'qa',baseRevision:t.revision,trip:clone(t),changes:['调整当天活动'],summary:'已生成调整',source:'model'}}
function indoorTrip(){const t=createTrip();['museum','art','louwailou'].forEach((id,i)=>{t.days[1]!.activities[i]!.placeId=id;t.days[1]!.activities[i]!.duration=30});t.days[1]!.start=780;return t}
describe('所有建议的时间自动协调',()=>{
 it('复现室内替换晚到20分钟：只缩短一个未锁定活动，保留17:00返程',async()=>{
  const before=createTrip(),old=clone(before),p=suggest(before,1,'下雨了，改成室内活动'),r=road(p.trip,1,[30,20,175]),routes={[routeKey(p.trip,1)]:r},load=vi.fn()
  const initial=clone(p);expect(proposalErrors(before,p.trip,routes).length).toBeGreaterThan(0)
  const fixed=await coordinateProposal(before,p,1,routes,load)
  expect(fixed.coordination?.resolved).toBe(true);expect(load).not.toHaveBeenCalled()
  expect(fixed.trip.days[1]!.activities[2]!.duration).toBe(70)
  expect(fixed.trip.days[1]!.activities.at(-1)).toEqual(before.days[1]!.activities.at(-1))
  expect(schedule(fixed.trip,1,r)[3]!.earliest).toBe(1020)
  expect(proposalErrors(before,fixed.trip,routes)).toEqual([])
  expect(fixed.changes.some(x=>x.includes('90 → 70'))).toBe(true)
  expect(fixed.trip.days[0]).toEqual(before.days[0]);expect(before).toEqual(old);expect(p).toEqual(initial)
  expect(lockedChanges(before,fixed.trip)).toEqual([]);expect(coordinationViolations(before,fixed,routes)).toEqual([])
 })
 it('已可行的建议不缩短、不删减，也不额外请求路线',async()=>{
  const before=createTrip(),p=suggest(before,0,'轻松一点'),r=road(p.trip,0,5),routes={[routeKey(p.trip,0)]:r},load=vi.fn()
  const result=await coordinateProposal(before,p,0,routes,load)
  expect(result).toBe(p);expect(load).not.toHaveBeenCalled()
 })
 it('顺延建议也可协调停留，固定时间的授权仍有效',async()=>{
  const before=createTrip(),r=road(before,0,[26,7,18,25]),routes={[routeKey(before,0)]:r},p=departureProposal(before,0,60,routes)
  const fixed=await coordinateProposal(before,p,0,routes,vi.fn())
  expect(fixed.coordination?.resolved).toBe(true);expect(fixed.trip.days[0]!.activities[2]!.fixedStart).toBe(780)
  expect(coordinationViolations(before,fixed,routes)).toEqual([])
  const selected=departureProposal(before,0,60,routes,{shiftFixed:true,extendEnd:false})
  const full=await coordinateProposal(before,selected,0,routes,vi.fn())
  expect(full.trip.days[0]!.activities.map(a=>a.duration)).toEqual(before.days[0]!.activities.map(a=>a.duration))
 })
 it('压缩不够时，重排方案必须重新核验真实道路',async()=>{
  const before=indoorTrip(),p=candidate(before),r=road(p.trip,1,[70,70,200]),routes={[routeKey(p.trip,1)]:r}
  const load=vi.fn(async(t:Trip,day:number)=>road(t,day,[10,10,100]))
  const fixed=await coordinateProposal(before,p,1,routes,load)
  expect(load).toHaveBeenCalledTimes(1);expect(fixed.coordination?.resolved).toBe(true)
  expect(fixed.trip.days[1]!.activities.length).toBe(4)
  expect(fixed.changes.some(x=>x.includes('重排'))).toBe(true)
  expect(proposalErrors(before,fixed.trip,routes,1)).toEqual([])
 })
 it('删减后仍保留至少一个游览点和返程，逐项披露删除',async()=>{
  const before=indoorTrip();before.days[1]!.activities.slice(0,3).forEach(a=>a.placeId='museum')
  const p=candidate(before),r=road(p.trip,1,100),routes={[routeKey(p.trip,1)]:r},load=vi.fn(async(t:Trip,day:number)=>road(t,day,100))
  const fixed=await coordinateProposal(before,p,1,routes,load)
  expect(fixed.coordination?.resolved).toBe(true);expect(load).toHaveBeenCalledTimes(2)
  expect(fixed.trip.days[1]!.activities.length).toBe(2)
  expect(fixed.changes.filter(x=>x.includes('移除未锁定'))).toHaveLength(2)
  expect(fixed.trip.days[1]!.activities.at(-1)).toEqual(before.days[1]!.activities.at(-1))
  expect(coordinationViolations(before,fixed,routes)).toEqual([])
 })
 it('全锁定时不篡改锁定，提供可确认的提前出发方案',async()=>{
  const before=indoorTrip();before.days[1]!.activities.slice(0,3).forEach(a=>a.lock='keep')
  const p=candidate(before),r=road(p.trip,1,100),routes={[routeKey(p.trip,1)]:r}
  const fixed=await coordinateProposal(before,p,1,routes,vi.fn())
  expect(fixed.coordination?.resolved).toBe(false);expect(fixed.coordination?.earlierStart).toBe(630)
  expect(fixed.trip).toEqual(before)
  expect(proposalErrors(before,fixed.trip,routes,fixed.coordination?.day).length).toBeGreaterThan(0)
  const earlier=earlierProposal(fixed)
  expect(earlier.trip.days[1]!.start).toBe(630)
  expect(proposalErrors(before,earlier.trip,routes,1)).toEqual([])
  expect(coordinationViolations(before,earlier,routes)).toEqual([])
  expect(before.days[1]!.start).toBe(780)
 })
 it('需求返程期限即使早于活动固定时间也有效',async()=>{
  const before=createTrip();before.requirements={city:'杭州',startDate:'2026-11-07',days:2,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[],destinationPlaceId:'station',returnBy:1000}
  const p=suggest(before,1,'下雨改成室内'),r=road(p.trip,1,[30,20,175]),routes={[routeKey(p.trip,1)]:r}
  const fixed=await coordinateProposal(before,p,1,routes,vi.fn())
  expect(fixed.coordination?.resolved).toBe(true)
  expect(schedule(fixed.trip,1,r).at(-1)!.earliest).toBe(1000)
  expect(fixed.trip.requirements!.returnBy).toBe(1000)
 })
 it('其他日期的旧冲突不干扰当前日的协调',async()=>{
  const before=createTrip();before.days[0]!.start=1000
  const p=suggest(before,1,'下雨改成室内'),r=road(p.trip,1,[30,20,175]),routes={[routeKey(p.trip,1)]:r}
  const fixed=await coordinateProposal(before,p,1,routes,vi.fn())
  expect(fixed.coordination?.resolved).toBe(true);expect(fixed.trip.days[0]).toEqual(before.days[0])
 })
 it('不会压缩固定活动之前已被等待吸收的时间',()=>{
  const t=createTrip();const day=t.days[0]!;day.start=300;day.end=900
  const r=road(t,0,10),routes={[routeKey(t,0)]:r}
  const fixed=fitDurations(t,0,routes)
  expect(fixed.days[0]!.activities[0]!.duration).toBe(day.activities[0]!.duration)
  expect(fixed.days[0]!.activities[1]!.duration).toBe(day.activities[1]!.duration)
 })
 it('缺失路线时不拿估算冒充核验',async()=>{
  const t=createTrip(),p=suggest(t,1,'室内'),load=vi.fn(async()=>{throw new Error('路线失败')})
  await expect(coordinateProposal(t,p,1,{},load)).rejects.toThrow('路线失败')
  expect(()=>fitDurations(p.trip,1,{})).toThrow('核验道路')
 })
 it('备选路线失败时不采用未核验的顺序',async()=>{
  const before=indoorTrip()
  const p=candidate(before),r=road(p.trip,1,100),routes={[routeKey(p.trip,1)]:r},load=vi.fn(async()=>{throw new Error('失败')})
  const result=await coordinateProposal(before,p,1,routes,load)
  expect(result.coordination?.resolved).toBe(false);expect(load).toHaveBeenCalled();expect(result.coordination!.warnings.length).toBeGreaterThan(0);expect(result.trip.days[1]!.activities).toEqual(p.trip.days[1]!.activities)
 })
 it('兼容真实模型响应的字段顺序、可选空值和地点快照规范化',async()=>{
  const before=createTrip();before.requirements={city:'杭州',startDate:'2026-11-07',days:2,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[],originPlaceId:null,destinationPlaceId:null,returnBy:null}
  const p=suggest(before,1,'室内');const r=road(p.trip,1,[30,20,175]),routes={[routeKey(p.trip,1)]:r}
  p.source='model';p.trip=Object.fromEntries(Object.entries(p.trip).reverse()) as Trip
  delete p.trip.requirements!.originPlaceId;delete p.trip.requirements!.destinationPlaceId;delete p.trip.requirements!.returnBy;p.trip.placeSnapshots=[]
  const result=await coordinateProposal(before,p,1,routes,vi.fn())
  expect(result.coordination?.resolved).toBe(true);expect(coordinationViolations(before,result,routes)).toEqual([])
 })
 it('应用前拒绝改返程、增加未知活动、改其他日期和过期版本',async()=>{
  const before=createTrip(),p=suggest(before,1,'室内'),r=road(p.trip,1,[30,20,175]),routes={[routeKey(p.trip,1)]:r}
  const result=await coordinateProposal(before,p,1,routes,vi.fn())
  const changed=clone(result);changed.trip.days[1]!.activities.at(-1)!.fixedStart=1100
  expect(coordinationViolations(before,changed,routes).length).toBeGreaterThan(0)
  const other=clone(result);other.trip.days[0]!.start++
  expect(coordinationViolations(before,other,routes).length).toBeGreaterThan(0)
  const below=clone(result);below.trip.days[1]!.activities[2]!.duration=15
  expect(coordinationViolations(before,below,routes).length).toBeGreaterThan(0)
  before.revision++;expect(coordinationViolations(before,result,routes).length).toBeGreaterThan(0)
 })
})

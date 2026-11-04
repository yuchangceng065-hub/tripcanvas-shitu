import { describe,it,expect } from 'vitest'
import { createTrip,suggest,lockedChanges,clone,validate,schedule } from './domain'
describe('规划约束',()=>{
 it('局部调整保留固定午餐和其他日期',()=>{const t=createTrip();const p=suggest(t,0,'轻松一点');expect(p.trip.days[1]).toEqual(t.days[1]);expect(lockedChanges(t,p.trip)).toEqual([]);expect(p.trip.days[0]!.activities.length).toBe(4)})
 it('拒绝删除必须保留的地点',()=>{const t=createTrip();t.days[0]!.activities[0]!.lock='keep';const after=clone(t);after.days[0]!.activities.shift();expect(lockedChanges(t,after).length).toBe(1)})
 it('拒绝移动固定安排到另一天',()=>{const t=createTrip();const after=clone(t);const a=after.days[0]!.activities.splice(2,1)[0]!;after.days[1]!.activities.push(a);expect(lockedChanges(t,after).length).toBeGreaterThan(0)})
 it('检测到站时间不可满足',()=>{const t=createTrip();t.days[1]!.start=1020;expect(validate(t).filter(x=>x.severity==='error').length).toBeGreaterThan(0)})
 it('输出不引用不存在的地点且不直接修改原计划',()=>{const t=createTrip();const before=clone(t);const p=suggest(t,0,'下雨改成室内');expect(t).toEqual(before);expect(schedule(p.trip,0).every(e=>!!e.place)).toBe(true)})
 it('未知指令明确提示而不伪造模型响应',()=>expect(()=>suggest(createTrip(),0,'订一张飞机票')).toThrow('规则规划'))
})

it('搜索地点快照可恢复坐标和时间轴',async()=>{
 const {registerPlaces,capturePlaces,placeById}=await import('./domain')
 const place={id:'osm:node:555',name:'真实搜索地点',kind:'地点',tag:'确认地点',description:'',duration:60,cost:0,lng:120.14,lat:30.25,x:0,y:0,indoor:false,image:'lake',crs:'WGS84'}
 registerPlaces([place]);const trip=createTrip();trip.days[0]!.activities[0]!.placeId=place.id
 expect(capturePlaces(trip).placeSnapshots?.[0]?.lng).toBe(120.14)
 expect(placeById(place.id).name).toBe(place.name);expect(schedule(trip,0)[0]!.place.name).toBe(place.name)
})

it('已保存方案在手工编辑后仍显示必去缺失，不能误报检查通过',()=>{
 const t=createTrip()
 t.requirements={city:'杭州',startDate:t.days[0]!.date,days:t.days.length,people:t.people,mode:t.mode,start:570,end:1110,pace:'balanced',interests:[],mustVisitIds:['quyuan']}
 t.days[0]!.activities=t.days[0]!.activities.filter(a=>a.placeId!=='quyuan')
 expect(validate(t).some(issue=>issue.id==='must-quyuan'&&issue.severity==='error')).toBe(true)
})

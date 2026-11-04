import { reactive } from 'vue'
import placeData from '../backend/places.json'
import { matchesRoute, routeKey, type RoadRoute, type RouteMap } from './routes'
export type Lock = 'none' | 'keep' | 'fixed'
export interface Place {id:string;name:string;kind:string;tag:string;description:string;duration:number;cost:number;lng:number;lat:number;x:number;y:number;indoor:boolean;image:string;coordinateSource?:string;crs?:string;address?:string;provider?:string;retrievedAt?:number;costKnown?:boolean;amapId?:string;restrictedAccess?:boolean;entrance?:[number,number];exit?:[number,number];routingLocation?:[number,number]}
export interface Activity {id:string;placeId:string;duration:number;lock:Lock;fixedStart?:number}
export interface Day {date:string;start:number;end:number;activities:Activity[]}
export interface Requirements {city:'杭州';startDate:string;days:number;people:number;mode:'walk'|'drive';start:number;end:number;pace:'relaxed'|'balanced'|'full';interests:string[];originPlaceId?:string|null;destinationPlaceId?:string|null;returnBy?:number|null;walkBudgetKm?:number|null}
export interface Mobility {parkingByPlace:Record<string,string>;parkingMinutes:number}
export interface Trip {id:string;title:string;city:string;people:number;mode:'walk'|'drive';days:Day[];revision:number;requirements?:Requirements|null;placeSnapshots?:Place[];mobility?:Mobility|null}
export interface Entry {activity:Activity;place:Place;start:number;end:number;earliest:number;travel:number;distance:number;conflict:boolean}
export interface Issue {id:string;message:string;severity:'error'|'info';day?:number}
export const places:Place[] = reactive(placeData as Place[])
export function registerPlaces(incoming:Place[]=[]){for(const p of incoming){if(!p||typeof p.id!=='string'||!p.id.startsWith('osm:')&&!p.id.startsWith('amap:')||!Number.isFinite(p.lng)||!Number.isFinite(p.lat)||(p.id.startsWith('amap:')?p.crs!=='GCJ02':p.crs!=='WGS84')||!p.name)continue;const index=places.findIndex(x=>x.id===p.id);if(index<0)places.push(p);else places[index]=p}try{localStorage.setItem('tripcanvas.places.v1',JSON.stringify(places.filter(p=>p.id.startsWith('osm:')||p.id.startsWith('amap:')).slice(-200)))}catch{}}
try{registerPlaces(JSON.parse(localStorage.getItem('tripcanvas.places.v1')||'[]'))}catch{}
export function capturePlaces(t:Trip){t.placeSnapshots=[...new Set([...t.days.flatMap(d=>d.activities.map(a=>a.placeId)),...Object.values(t.mobility?.parkingByPlace||{})])].filter(id=>id.startsWith('osm:')||id.startsWith('amap:')).map(id=>placeById(id));return t}
export const placeById=(id:string)=>places.find(p=>p.id===id)!
export const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value))
export const uid=()=>crypto.randomUUID()
export function createTrip(title='杭州，湖畔的两日慢游',start='2026-11-07',count=2):Trip {
 const groups=[['quyuan','museum','louwailou','baidi','hubin'],['liulang','leifeng','art','station'],['sudi','silk','coffee']]
 return {id:uid(),title,city:'杭州',people:2,mode:'walk',revision:1,days:Array.from({length:count},(_,i)=>{const d=new Date(start+'T12:00:00');d.setDate(d.getDate()+i);return {date:d.toISOString().slice(0,10),start:570,end:i===1?1050:1110,activities:groups[i%3]!.map((p,j)=>({id:uid(),placeId:p,duration:placeById(p).duration,lock:p==='louwailou'?'fixed':p==='station'?'fixed':'none',fixedStart:p==='louwailou'?780:p==='station'?1020:undefined}))}})}
}
export function route(a:Place,b:Place,mode:Trip['mode']) {const ac=a.crs==='GCJ02'?[a.lng,a.lat]:a.routingLocation,bc=b.crs==='GCJ02'?[b.lng,b.lat]:b.routingLocation;if(ac&&bc){a={...a,lng:ac[0]!,lat:ac[1]!};b={...b,lng:bc[0]!,lat:bc[1]!}}const r=Math.PI/180;const h=Math.sin((b.lat-a.lat)*r/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin((b.lng-a.lng)*r/2)**2;const distance=6371*2*Math.asin(Math.sqrt(h))*1.25;return {distance,minutes:Math.max(5,Math.round(distance/(mode==='walk'?4.5:23)*60+(mode==='drive'?8:0)))}}
export function schedule(trip:Trip,dayIndex:number,roads?:RoadRoute):Entry[]{const day=trip.days[dayIndex];if(!day)return [];let end=day.start;return day.activities.map((a,i)=>{const p=placeById(a.placeId);const travel=i?(matchesRoute(trip,dayIndex,roads)?roads!.legs[i-1]!:route(placeById(day.activities[i-1]!.placeId),p,trip.mode)):{distance:0,minutes:0};const earliest=end+travel.minutes;const fixed=a.lock==='fixed'?a.fixedStart:undefined;const start=Math.max(earliest,fixed??earliest);end=start+a.duration;return {activity:a,place:p,start,end,earliest,travel:travel.minutes,distance:travel.distance,conflict:(fixed!==undefined&&earliest>fixed)||end>day.end}})}
export const time=(m:number)=>`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`
export function validate(trip:Trip,routes:RouteMap={}):Issue[]{const issues:Issue[]=[];trip.days.forEach((day,i)=>{if(day.start>=day.end)issues.push({id:`day-range-${i}`,day:i,message:`第 ${i+1} 天出发时间 ${time(day.start)} 不早于结束上限 ${time(day.end)}，请选择延后结束或缩短顺延时长`,severity:'error'});for(const e of schedule(trip,i,routes[routeKey(trip,i)])){const fixed=e.activity.lock==='fixed'?e.activity.fixedStart:undefined;if(fixed!==undefined&&e.earliest>fixed)issues.push({id:e.activity.id,day:i,message:`第 ${i+1} 天 · ${e.place.name} 最早 ${time(e.earliest)} 到达，比固定时间 ${time(fixed)} 晚 ${e.earliest-fixed} 分钟`,severity:'error'});if(e.end>day.end)issues.push({id:e.activity.id,day:i,message:`第 ${i+1} 天 · ${e.place.name} 将于 ${time(e.end)} 结束，超过当天 ${time(day.end)} 的结束时间 ${e.end-day.end} 分钟`,severity:'error'});if(e.activity.duration<15||e.activity.duration>360)issues.push({id:e.activity.id,day:i,message:'停留时间应在 15—360 分钟之间',severity:'error'})}if(day.activities.length>6)issues.push({id:`day${i}`,day:i,message:`第 ${i+1} 天安排较多，建议留出休息时间`,severity:'info'})});const r=trip.requirements;if(r&&r.returnBy!==null&&r.returnBy!==undefined){const di=trip.days.length-1;const list=schedule(trip,di,routes[routeKey(trip,di)]);const index=list.findIndex(e=>e.place.id===r.destinationPlaceId);const arrival=index<0?Infinity:list[index]!.earliest;if(arrival>r.returnBy)issues.push({id:'return-constraint',day:di,message:index<0?'末日返程地点缺失':`第 ${di+1} 天 · ${list[index]!.place.name} 最早 ${time(arrival)} 到达，比返程期限 ${time(r.returnBy)} 晚 ${arrival-r.returnBy} 分钟`,severity:'error'})}return issues}
export function lockedChanges(before:Trip,after:Trip):string[]{const errors:string[]=[];before.days.forEach((d,di)=>d.activities.forEach(a=>{if(a.lock==='none')return;const next=after.days.flatMap(x=>x.activities).find(x=>x.id===a.id);if(!next||next.placeId!==a.placeId||next.lock!==a.lock)errors.push(`${placeById(a.placeId).name} 已锁定`);if(a.lock==='fixed'&&next&&(after.days[di]?.date!==d.date||!after.days[di]?.activities.some(x=>x.id===a.id)||next.fixedStart!==a.fixedStart||next.duration!==a.duration))errors.push(`${placeById(a.placeId).name} 的固定安排不可修改`)}));return errors}
export interface DepartureShift {day:number;delay:number;shiftFixed:boolean;extendEnd:boolean}
export interface Coordination {day:number;basis:Trip;changes:string[];resolved:boolean;earlierStart?:number;warnings:string[]}
export interface Proposal {id:string;baseRevision:number;trip:Trip;summary:string;changes:string[];source:'demo'|'model'|'schedule';departure?:DepartureShift;coordination?:Coordination;visit?:{day:number;placeId:string;query:string};transport?:{day:number}}
// Only these simple departure requests use arithmetic; mixed requests still go to the assistant.
export function departureDelay(prompt:string):number|null {
 if(!/晚.*出发|出发.*晚|推迟.*出发|出发.*推迟|多睡/.test(prompt)||/不要|不想|别|室内|下雨|减少|删除|去掉|换成|增加|添加/.test(prompt))return null
 const amount=prompt.match(/(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(?:个)?\s*(半)?\s*(小时|分钟)/)
 if(!amount)return /半(?:个)?小时/.test(prompt)?30:60
 const numbers:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
 const raw=amount[1]!,parts=raw.split('十')
 const n=Number(raw)||numbers[raw]||(parts.length===2?(parts[0]?numbers[parts[0]]||0:1)*10+(parts[1]?numbers[parts[1]]||0:0):0)
 const delay=(n+(amount[2]?0.5:0))*(amount[3]==='小时'?60:1)
 if(!Number.isInteger(delay)||delay<1||delay>720)throw new Error('请使用 1—720 分钟内的顺延时长')
 return delay
}
export function returnAnchor(trip:Trip,day:number,a:Activity):boolean {return placeById(a.placeId).kind==='返程'||(day===trip.days.length-1&&trip.requirements?.returnBy!=null&&a.placeId===trip.requirements.destinationPlaceId)}
export function departureProposal(before:Trip,dayIndex:number,delay:number,routes:RouteMap={},options={shiftFixed:false,extendEnd:false}):Proposal {
 const original=before.days[dayIndex]
 if(!original||!Number.isInteger(delay)||delay<1||delay>720||original.start+delay>=1440)throw new Error('顺延后超出当天，请调整出发时间')
 const next=clone(before),day=next.days[dayIndex]!,roads=routes[routeKey(before,dayIndex)]
 day.start+=delay
 const changes=[`出发 ${time(original.start)} → ${time(day.start)}；后续活动按交通与停留时间重排`]
 let end=day.start
 for(let i=0;i<day.activities.length;i++){
  const a=day.activities[i]!,previous=day.activities[i-1]
  const travel=i?(matchesRoute(next,dayIndex,roads)?roads!.legs[i-1]!.minutes:route(placeById(previous!.placeId),placeById(a.placeId),next.mode).minutes):0
  const earliest=end+travel
  if(options.shiftFixed&&a.lock==='fixed'&&a.fixedStart!==undefined&&!returnAnchor(before,dayIndex,a)){
   const from=a.fixedStart;a.fixedStart=Math.max(from+delay,earliest)
   changes.push(`${placeById(a.placeId).name} 固定时间 ${time(from)} → ${time(a.fixedStart)}，确认后固定在新时间`)
  }
  end=Math.max(earliest,a.lock==='fixed'?a.fixedStart??earliest:earliest)+a.duration
 }
 if(options.extendEnd&&end>day.end){day.end=Math.min(1440,Math.max(end,day.start+1));changes.push(`当天结束上限 ${time(original.end)} → ${time(day.end)}`)}
 if(original.activities.some(a=>returnAnchor(before,dayIndex,a)))changes.push('返程固定时间与最晚到达期限保持不变')
 return {id:uid(),baseRevision:before.revision,trip:next,summary:'后续安排已重新计算，请核对逐站时间后确认。',changes,source:'schedule',departure:{day:dayIndex,delay,...options}}
}
export function validDepartureProposal(before:Trip,p:Proposal,routes:RouteMap={}):boolean {
 if(p.source!=='schedule'||!p.departure||p.baseRevision!==before.revision)return false
 try{const {day,delay,shiftFixed,extendEnd}=p.departure;return JSON.stringify(departureProposal(before,day,delay,routes,{shiftFixed,extendEnd}).trip)===JSON.stringify(p.trip)}catch{return false}
}
export function proposalErrors(before:Trip,after:Trip,routes:RouteMap={},includeDay?:number):Issue[] {
 const changed=new Set(after.days.flatMap((d,i)=>JSON.stringify(d)!==JSON.stringify(before.days[i])?[i]:[]))
 if(includeDay!==undefined)changed.add(includeDay)
 return validate(after,routes).filter(issue=>issue.severity==='error'&&(issue.day===undefined||changed.has(issue.day)))
}
export function suggest(trip:Trip,dayIndex:number,prompt:string):Proposal {const delay=departureDelay(prompt);if(delay!==null)return departureProposal(trip,dayIndex,delay);const next=clone(trip);const day=next.days[dayIndex]!;const changes:string[]=[];
 if(/室内|雨|下雨/.test(prompt)){const available=places.filter(p=>p.indoor&&p.kind!=='返程'&&!day.activities.some(a=>a.placeId===p.id));day.activities.forEach(a=>{const p=placeById(a.placeId);if(!p.indoor&&a.lock==='none'&&available.length){const replacement=available.shift()!;changes.push(`${p.name} → ${replacement.name}`);a.placeId=replacement.id;a.duration=Math.min(a.duration,replacement.duration)}})}
 else if(/轻松|减少|休息|留白|赶|慢|午休/.test(prompt)){const index=day.activities.findLastIndex(a=>a.lock==='none'&&placeById(a.placeId).kind!=='返程');if(index>=0){const removed=day.activities.splice(index,1)[0]!;changes.push(`移除 ${placeById(removed.placeId).name}，留出 ${removed.duration} 分钟自由时间`)}}
 else throw new Error('当前使用规则规划。可输入“我想去灵隐寺”搜索并加入地点，或使用“轻松一点”“改成室内”“晚出发一小时”；自由对话将在接入 AI 后开放。')
 if(!changes.length)throw new Error('当前没有符合条件且未锁定的活动。你可以先调整锁定状态。')
 const errors=lockedChanges(trip,next);if(errors.length)throw new Error(errors.join('；'));
 return {id:uid(),baseRevision:trip.revision,trip:next,summary:/室内|雨/.test(prompt)?'把湖边的时光，换成室内的小发现。':/晚|睡|出发/.test(prompt)?'多睡一会儿，也是一种旅行方式。':'少赶一站，多留一点自在。',changes,source:'demo'}
}
export function safeSnapshot(trip:Trip):Trip {const t=clone(trip);return t}

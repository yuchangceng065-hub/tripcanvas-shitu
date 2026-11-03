import placeData from '../backend/places.json'
import { matchesRoute, routeKey, type RoadRoute, type RouteMap } from './routes'
export type Lock = 'none' | 'keep' | 'fixed'
export interface Place {id:string;name:string;kind:string;tag:string;description:string;duration:number;cost:number;lng:number;lat:number;x:number;y:number;indoor:boolean;image:string;coordinateSource?:string;crs?:string}
export interface Activity {id:string;placeId:string;duration:number;lock:Lock;fixedStart?:number}
export interface Day {date:string;start:number;end:number;activities:Activity[]}
export interface Trip {id:string;title:string;city:string;people:number;mode:'walk'|'drive';days:Day[];revision:number}
export interface Entry {activity:Activity;place:Place;start:number;end:number;travel:number;distance:number;conflict:boolean}
export interface Issue {id:string;message:string;severity:'error'|'info';day?:number}
export const places:Place[] = placeData
export const placeById=(id:string)=>places.find(p=>p.id===id)!
export const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value))
export const uid=()=>crypto.randomUUID()
export function createTrip(title='杭州，湖畔的两日慢游',start='2026-11-07',count=2):Trip {
 const groups=[['quyuan','museum','louwailou','baidi','hubin'],['liulang','leifeng','art','station'],['sudi','silk','coffee']]
 return {id:uid(),title,city:'杭州',people:2,mode:'walk',revision:1,days:Array.from({length:count},(_,i)=>{const d=new Date(start+'T12:00:00');d.setDate(d.getDate()+i);return {date:d.toISOString().slice(0,10),start:570,end:i===1?1050:1110,activities:groups[i%3]!.map((p,j)=>({id:uid(),placeId:p,duration:placeById(p).duration,lock:p==='louwailou'?'fixed':p==='station'?'fixed':'none',fixedStart:p==='louwailou'?780:p==='station'?1020:undefined}))}})}
}
export function route(a:Place,b:Place,mode:Trip['mode']) {const r=Math.PI/180;const h=Math.sin((b.lat-a.lat)*r/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin((b.lng-a.lng)*r/2)**2;const distance=6371*2*Math.asin(Math.sqrt(h))*1.25;return {distance,minutes:Math.max(5,Math.round(distance/(mode==='walk'?4.5:23)*60+(mode==='drive'?8:0)))}}
export function schedule(trip:Trip,dayIndex:number,roads?:RoadRoute):Entry[]{const day=trip.days[dayIndex];if(!day)return [];let end=day.start;return day.activities.map((a,i)=>{const p=placeById(a.placeId);const travel=i?(matchesRoute(trip,dayIndex,roads)?roads!.legs[i-1]!:route(placeById(day.activities[i-1]!.placeId),p,trip.mode)):{distance:0,minutes:0};const earliest=end+travel.minutes;const start=a.lock==='fixed'&&a.fixedStart!==undefined?a.fixedStart:earliest;end=start+a.duration;return {activity:a,place:p,start,end,travel:travel.minutes,distance:travel.distance,conflict:start<earliest||end>day.end}})}
export const time=(m:number)=>`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`
export function validate(trip:Trip,routes:RouteMap={}):Issue[]{const issues:Issue[]=[];trip.days.forEach((day,i)=>{for(const e of schedule(trip,i,routes[routeKey(trip,i)])){if(e.conflict)issues.push({id:e.activity.id,day:i,message:`第 ${i+1} 天 · ${e.place.name} 与相邻活动或当天结束时间冲突`,severity:'error'});if(e.activity.duration<15||e.activity.duration>360)issues.push({id:e.activity.id,day:i,message:'停留时间应在 15—360 分钟之间',severity:'error'})}if(day.activities.length>6)issues.push({id:`day${i}`,day:i,message:`第 ${i+1} 天安排较多，建议留出休息时间`,severity:'info'})});return issues}
export function lockedChanges(before:Trip,after:Trip):string[]{const errors:string[]=[];before.days.forEach((d,di)=>d.activities.forEach(a=>{if(a.lock==='none')return;const next=after.days.flatMap(x=>x.activities).find(x=>x.id===a.id);if(!next||next.placeId!==a.placeId||next.lock!==a.lock)errors.push(`${placeById(a.placeId).name} 已锁定`);if(a.lock==='fixed'&&next&&(after.days[di]?.date!==d.date||!after.days[di]?.activities.some(x=>x.id===a.id)||next.fixedStart!==a.fixedStart||next.duration!==a.duration))errors.push(`${placeById(a.placeId).name} 的固定安排不可修改`)}));return errors}
export interface Proposal {id:string;baseRevision:number;trip:Trip;summary:string;changes:string[];source:'demo'|'model'}
export function suggest(trip:Trip,dayIndex:number,prompt:string):Proposal {const next=clone(trip);const day=next.days[dayIndex]!;const changes:string[]=[];
 if(/室内|雨|下雨/.test(prompt)){const available=places.filter(p=>p.indoor&&p.kind!=='返程'&&!day.activities.some(a=>a.placeId===p.id));day.activities.forEach(a=>{const p=placeById(a.placeId);if(!p.indoor&&a.lock==='none'&&available.length){const replacement=available.shift()!;changes.push(`${p.name} → ${replacement.name}`);a.placeId=replacement.id;a.duration=Math.min(a.duration,replacement.duration)}})}
 else if(/晚|睡|出发/.test(prompt)){day.start+=60;changes.push(`当天出发时间调整为 ${time(day.start)}`)}
 else if(/轻松|减少|休息|留白|赶|慢|午休/.test(prompt)){const index=day.activities.findLastIndex(a=>a.lock==='none'&&placeById(a.placeId).kind!=='返程');if(index>=0){const removed=day.activities.splice(index,1)[0]!;changes.push(`移除 ${placeById(removed.placeId).name}，留出 ${removed.duration} 分钟自由时间`)}}
 else throw new Error('示例助手支持“轻松一点”“改成室内”“晚出发一小时”。自由对话需要连接模型服务。')
 if(!changes.length)throw new Error('当前没有符合条件且未锁定的活动。你可以先调整锁定状态。')
 const errors=lockedChanges(trip,next);if(errors.length)throw new Error(errors.join('；'));
 return {id:uid(),baseRevision:trip.revision,trip:next,summary:/室内|雨/.test(prompt)?'把湖边的时光，换成室内的小发现。':/晚|睡|出发/.test(prompt)?'多睡一会儿，也是一种旅行方式。':'少赶一站，多留一点自在。',changes,source:'demo'}
}
export function safeSnapshot(trip:Trip):Trip {const t=clone(trip);return t}

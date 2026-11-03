export type Lock = 'none' | 'keep' | 'fixed'
export interface Place {id:string;name:string;kind:string;tag:string;description:string;duration:number;cost:number;lng:number;lat:number;x:number;y:number;indoor:boolean;image:string}
export interface Activity {id:string;placeId:string;duration:number;lock:Lock;fixedStart?:number}
export interface Day {date:string;start:number;end:number;activities:Activity[]}
export interface Trip {id:string;title:string;city:string;people:number;mode:'walk'|'drive';days:Day[];revision:number}
export interface Entry {activity:Activity;place:Place;start:number;end:number;travel:number;distance:number;conflict:boolean}
export interface Issue {id:string;message:string;severity:'error'|'info'}
export const places:Place[] = [
{id:'quyuan',name:'曲院风荷',kind:'自然风景',tag:'湖边慢走',description:'沿着湖岸和小桥散步，把上午留给树影、微风与水面。',duration:90,cost:0,lng:120.1368,lat:30.2520,x:230,y:190,indoor:false,image:'lake'},
{id:'museum',name:'浙江省博物馆 · 孤山馆区',kind:'博物馆',tag:'人文探索',description:'在孤山脚下留一段安静的看展时间。实际开放与预约情况请在出发前确认。',duration:90,cost:0,lng:120.1435,lat:30.2528,x:410,y:190,indoor:true,image:'museum'},
{id:'louwailou',name:'楼外楼 · 孤山路店',kind:'杭帮菜',tag:'午餐时间',description:'把午间留给一顿从容的杭帮菜。餐费为个人预算，非实时菜单价格。',duration:75,cost:120,lng:120.1416,lat:30.2512,x:362,y:224,indoor:true,image:'food'},
{id:'baidi',name:'白堤',kind:'城市漫步',tag:'湖光山色',description:'从平湖秋月走向断桥，途中不妨找一张长椅坐一会儿。',duration:75,cost:0,lng:120.1511,lat:30.2571,x:510,y:153,indoor:false,image:'lake'},
{id:'hubin',name:'湖滨步行街',kind:'街区探索',tag:'日落时分',description:'沿着东岸慢慢走，在城市和湖水之间收集傍晚的颜色。',duration:60,cost:40,lng:120.1612,lat:30.2536,x:651,y:277,indoor:false,image:'city'},
{id:'liulang',name:'柳浪闻莺',kind:'自然风景',tag:'清晨散步',description:'沿湖绿地与垂柳小径，适合留出一段不赶时间的散步。',duration:80,cost:0,lng:120.1561,lat:30.2333,x:610,y:544,indoor:false,image:'forest'},
{id:'leifeng',name:'雷峰塔',kind:'文化古迹',tag:'西湖全景',description:'走近西湖南岸的文化地标。票价和开放信息需在出发前核实。',duration:90,cost:40,lng:120.1496,lat:30.2301,x:478,y:590,indoor:false,image:'museum'},
{id:'silk',name:'中国丝绸博物馆',kind:'博物馆',tag:'室内备选',description:'从织物与纹样开始，了解丝绸的历史。请自行确认预约和开放安排。',duration:100,cost:0,lng:120.1485,lat:30.2209,x:450,y:675,indoor:true,image:'museum'},
{id:'art',name:'浙江美术馆',kind:'美术馆',tag:'艺术留白',description:'在西湖南岸看展，给旅行留一点安静的时间。展览信息以官方为准。',duration:90,cost:0,lng:120.1546,lat:30.2306,x:570,y:600,indoor:true,image:'art'},
{id:'coffee',name:'湖畔咖啡时光',kind:'休息',tag:'自选咖啡店',description:'这是可编辑的示例休息点，请在出发前选定实际咖啡店。',duration:60,cost:45,lng:120.1598,lat:30.2528,x:640,y:314,indoor:true,image:'coffee'},
{id:'sudi',name:'苏堤',kind:'城市漫步',tag:'树影与湖风',description:'沿着长堤漫步，适当留出休息时间。',duration:110,cost:0,lng:120.1369,lat:30.2403,x:264,y:408,indoor:false,image:'forest'},
{id:'station',name:'杭州东站',kind:'返程',tag:'固定返程',description:'这是行程的到站时间约束，不包含购票。请为进站安检另留余量。',duration:30,cost:0,lng:120.2128,lat:30.2909,x:795,y:90,indoor:true,image:'city'},
]
export const placeById=(id:string)=>places.find(p=>p.id===id)!
export const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value))
export const uid=()=>crypto.randomUUID()
export function createTrip(title='杭州，湖畔的两日慢游',start='2026-11-07',count=2):Trip {
 const groups=[['quyuan','museum','louwailou','baidi','hubin'],['liulang','leifeng','art','station'],['sudi','silk','coffee']]
 return {id:uid(),title,city:'杭州',people:2,mode:'walk',revision:1,days:Array.from({length:count},(_,i)=>{const d=new Date(start+'T12:00:00');d.setDate(d.getDate()+i);return {date:d.toISOString().slice(0,10),start:570,end:i===1?1050:1110,activities:groups[i%3]!.map((p,j)=>({id:uid(),placeId:p,duration:placeById(p).duration,lock:p==='louwailou'?'fixed':p==='station'?'fixed':'none',fixedStart:p==='louwailou'?780:p==='station'?1020:undefined}))}})}
}
export function route(a:Place,b:Place,mode:Trip['mode']) {const r=Math.PI/180;const h=Math.sin((b.lat-a.lat)*r/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin((b.lng-a.lng)*r/2)**2;const distance=6371*2*Math.asin(Math.sqrt(h))*1.25;return {distance,minutes:Math.max(5,Math.round(distance/(mode==='walk'?4.5:23)*60+(mode==='drive'?8:0)))}}
export function schedule(trip:Trip,dayIndex:number):Entry[]{const day=trip.days[dayIndex];if(!day)return [];let end=day.start;return day.activities.map((a,i)=>{const p=placeById(a.placeId);const travel=i?route(placeById(day.activities[i-1]!.placeId),p,trip.mode):{distance:0,minutes:0};const earliest=end+travel.minutes;const start=a.lock==='fixed'&&a.fixedStart!==undefined?a.fixedStart:earliest;end=start+a.duration;return {activity:a,place:p,start,end,travel:travel.minutes,distance:travel.distance,conflict:start<earliest||end>day.end}})}
export const time=(m:number)=>`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`
export function validate(trip:Trip):Issue[]{const issues:Issue[]=[];trip.days.forEach((day,i)=>{for(const e of schedule(trip,i)){if(e.conflict)issues.push({id:e.activity.id,message:`第 ${i+1} 天 · ${e.place.name} 与相邻活动或当天结束时间冲突`,severity:'error'});if(e.activity.duration<15||e.activity.duration>360)issues.push({id:e.activity.id,message:'停留时间应在 15—360 分钟之间',severity:'error'})}if(day.activities.length>6)issues.push({id:`day${i}`,message:`第 ${i+1} 天安排较多，建议留出休息时间`,severity:'info'})});return issues}
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

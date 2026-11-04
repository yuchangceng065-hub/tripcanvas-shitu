<script setup lang="ts">
import { ref,computed,onMounted,onUnmounted,nextTick,watch } from 'vue'
import { Map,Compass,Heart,Plus,ArrowUpRight,ChevronDown,ChevronRight,ChevronLeft,CalendarDays,Users,Share2,History,Sparkles,MapPin,Clock,Footprints,Car,Check,LockKeyhole,LockKeyholeOpen,GripVertical,MoreHorizontal,X,Send,ArrowRight,ArrowDown,ArrowUp,CloudRain,Coffee,Leaf,Sun,Search,Settings2,Trash2,Copy,Download,RotateCcw,AlertTriangle,CheckCircle2,Info,Bookmark,ExternalLink,LoaderCircle,LogIn,Menu,ShieldCheck } from '@lucide/vue'
import TripMap from './components/TripMap.vue'
import PlacePicker from './components/PlacePicker.vue'
import TripBuilder from './components/TripBuilder.vue'
import {coordinateProposal,coordinationViolations,earlierProposal} from './coordination'
import {extractVisitQuery,normalizePlaceQuery,visitProposal} from './placeIntent'
import {handleComposerKeydown} from './composerKeyboard'
import {credentialsFromForm} from './authForm'
import {ApiError} from './apiError'
import { useTrips } from './store'
import { places,placeById,createTrip,clone,uid,time,schedule,validate,suggest,lockedChanges,registerPlaces,capturePlaces,departureDelay,departureProposal,validDepartureProposal,proposalErrors,returnAnchor,type Trip,type Activity,type Proposal,type Place } from './domain'
import {checkAPI,api,remoteSuggestion,setToken,connection,backendConfigured} from './api'
import {useRoadRoutes,routeKey,loadRoute,type RouteMap} from './routes'
const store=useTrips()
const page=ref<'canvas'|'trips'|'favorites'>('canvas'),dayIndex=ref(0),selected=ref(''),modal=ref(''),toast=ref(''),search=ref(''),dragging=ref(''),tabMobile=ref('plan')
const trip=computed(()=>store.trips.find(t=>t.id===store.activeId)||store.trips[0]!)
const {routes:roadRoutes,state:routeState,refresh:refreshRoute}=useRoadRoutes(trip,dayIndex)
const entries=computed(()=>schedule(trip.value,dayIndex.value,roadRoutes.value[routeKey(trip.value,dayIndex.value)]))
const routeReady=computed(()=>routeState.value.status==='ready'||entries.value.length<2)
const checkedDays=computed(()=>trip.value.days.filter((d,i)=>d.activities.length<2||roadRoutes.value[routeKey(trip.value,i)]).length)
const selectedEntry=computed(()=>entries.value.find(e=>e.activity.id===selected.value))
const allIssues=computed(()=>validate(trip.value,roadRoutes.value)),errors=computed(()=>allIssues.value.filter(x=>x.severity==='error'))
const totalTravel=computed(()=>entries.value.reduce((s,e)=>s+e.travel,0)),distance=computed(()=>entries.value.reduce((s,e)=>s+e.distance,0)),budget=computed(()=>entries.value.reduce((s,e)=>s+e.place.cost,0))
const daysLabel=computed(()=>`${shortDate(trip.value.days[0]!.date)} — ${shortDate(trip.value.days.at(-1)!.date)}`)
const proposal=ref<Proposal|null>(null),prompt=ref(''),busy=ref(false),response=ref(''),backend=computed(()=>connection.status==='online'),hasModel=computed(()=>connection.model),user=ref<{username:string}|null>(null),loginMode=ref(false),username=ref(''),password=ref(''),authError=ref(''),accountBusy=ref(false)
const proposalRoutes=ref<RouteMap>({}),proposalRouteError=ref('')
const busyText=ref('正在检查可调整的安排…')
const visitSearch=ref<{query:string;places:Place[];tripId:string;revision:number;day:number}|null>(null),visitLookupQuery=ref('')
const composerInput=ref<HTMLTextAreaElement|null>(null),submittedPrompt=ref('')
const proposalIssues=computed(()=>proposal.value?proposalErrors(trip.value,proposal.value.trip,proposalRoutes.value,proposal.value.coordination?.day):[])
const departureRows=computed(()=>{const p=proposal.value;if(!p?.departure&&!p?.coordination&&!p?.visit)return [];const day=p.departure?.day??p.coordination?.day??p.visit!.day;const old=schedule(trip.value,day,proposalRoutes.value[routeKey(trip.value,day)]),next=schedule(p.trip,day,proposalRoutes.value[routeKey(p.trip,day)]);return [...next.map(e=>({id:e.activity.id,name:e.place.name,before:old.find(o=>o.activity.id===e.activity.id),after:e})),...old.filter(e=>!next.some(n=>n.activity.id===e.activity.id)).map(e=>({id:e.activity.id,name:e.place.name,before:e,after:undefined}))]})
const visitEntry=computed(()=>{const p=proposal.value;if(!p?.visit)return;return schedule(p.trip,p.visit.day,proposalRoutes.value[routeKey(p.trip,p.visit.day)]).find(e=>e.place.id===p.visit!.placeId)})
const canShiftFixed=computed(()=>proposal.value?.departure&&trip.value.days[proposal.value.departure.day]!.activities.some(a=>a.lock==='fixed'&&!returnAnchor(trip.value,proposal.value!.departure!.day,a)))
const builderKey=ref(0),resumeBuilder=ref(false)
const assistantScroll=ref<HTMLElement|null>(null)
watch([busy,response],async()=>{
 await nextTick()
 const panel=assistantScroll.value;if(!panel)return
 const card=busy.value?panel.querySelector<HTMLElement>('.assistant-message:not(.response)'):visitSearch.value?panel.querySelector<HTMLElement>('.visit-search-card'):proposal.value?.departure||proposal.value?.coordination||proposal.value?.visit?panel.querySelector<HTMLElement>('.proposal-card'):null
 if(card)panel.scrollTop+=card.getBoundingClientRect().top-panel.getBoundingClientRect().top
 else panel.scrollTop=panel.scrollHeight
})
function onPromptKeydown(event:KeyboardEvent){
 handleComposerKeydown(event,text=>{prompt.value=text;void generate(text)})
}
function syncComposerValue(event:Event){prompt.value=(event.target as HTMLTextAreaElement).value}
function submitComposer(){const text=composerInput.value?.value??prompt.value;prompt.value=text;void generate(text)}
function acceptDraft(t:Trip){registerPlaces(t.placeSnapshots);store.add(t);syncTrip(t);openTrip(t.id);modal.value='';builderKey.value++;notify('旅行草案已确认保存')}
const deletingTrip=ref<string|null>(null)
async function deleteTrip(){if(!deletingTrip.value)return;const id=deletingTrip.value;if(backend.value&&user.value){try{await api('/trips/'+id,'DELETE')}catch(e){notify((e as Error).message);return}}store.remove(id);modal.value='';deletingTrip.value=null;notify('旅行已删除')}
const newTitle=ref(''),newDate=ref('2026-11-14'),newDays=ref(2),newPeople=ref(2),newIntent=ref(''),newError=ref('')
const editActivity=ref<Activity|null>(null),editDuration=ref(60),editDate=ref(0)
const savedAt=ref('已保存在此设备'),shareUrl=ref(''),shareId=ref(''),shareBusy=ref(false),shareTrip=ref<Trip|null>(null)
const filteredPlaces=computed(()=>places.filter(p=>p.kind!=='返程'&&(p.name.includes(search.value)||p.kind.includes(search.value)||p.tag.includes(search.value))))
let toastTimer:ReturnType<typeof setTimeout>
function notify(message:string){toast.value=message;clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.value='',3600)}
function closeShare(){shareTrip.value=null;window.location.hash=''}
function shortDate(d:string){return `${Number(d.slice(5,7))}月${Number(d.slice(8))}日`}
function weekday(d:string){return ['周日','周一','周二','周三','周四','周五','周六'][new Date(d+'T12:00:00').getDay()]}
function imageFor(kind:string){return `/assets/${({food:'coffee',city:'museum',art:'museum'} as Record<string,string>)[kind]||kind}.jpg`}
function onKeyboard(event:KeyboardEvent){if(!modal.value)return;if(event.key==='Escape'){modal.value='';return}if(event.key==='Tab'){const el=Array.from(document.querySelectorAll<HTMLElement>('.modal')).find(el=>el.offsetParent!==null);const controls=Array.from(el?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea,select,a[href]')||[]).filter(e=>e.offsetParent!==null);const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}}}
watch(modal,async value=>{if(value){await nextTick();Array.from(document.querySelectorAll<HTMLElement>('.modal input,.modal textarea,.modal button')).find(el=>el.offsetParent!==null)?.focus()}})
onMounted(()=>document.addEventListener('keydown',onKeyboard));onUnmounted(()=>document.removeEventListener('keydown',onKeyboard))
function openModal(name:string){modal.value=name;search.value='';authError.value=''}
function changeDay(i:number){submittedPrompt.value='';dayIndex.value=i;selected.value=entries.value[0]?.activity.id||'';proposal.value=null;response.value=''}
function openTrip(id:string){response.value='';submittedPrompt.value='';visitSearch.value=null;store.activeId=id;dayIndex.value=0;page.value='canvas';selected.value=entries.value[0]?.activity.id||'';proposal.value=null}
watch(()=>[trip.value.id,trip.value.revision,dayIndex.value],()=>{visitSearch.value=null})
function commit(next:Trip,label:string,expected=trip.value.revision){try{if(next.requirements){next.requirements.mode=next.mode;next.requirements.people=next.people}capturePlaces(next);store.commit(next,label,expected);savedAt.value='刚刚保存';proposal.value=null;syncTrip(next);return true}catch(e){notify((e as Error).message);return false}}
async function syncTrip(t:Trip){if(backend.value&&user.value){try{await api('/trips/'+t.id,'PUT',{trip:t});savedAt.value='已同步到云端'}catch{savedAt.value='已保存本地 · 同步失败'}}}
function toggleLock(id:string){const n=clone(trip.value);const a=n.days[dayIndex.value]!.activities.find(a=>a.id===id)!;const start=entries.value.find(e=>e.activity.id===id)!.start;a.lock=a.lock==='none'?'keep':a.lock==='keep'?'fixed':'none';a.fixedStart=a.lock==='fixed'?start:undefined;commit(n,'更新锁定条件');notify(a.lock==='none'?'已解除锁定':a.lock==='keep'?'已设为必须保留，再点一次可固定时间':'已固定地点和时间')}
function move(id:string,delta:number){const n=clone(trip.value);const a=n.days[dayIndex.value]!.activities;const i=a.findIndex(x=>x.id===id);if(i<0||i+delta<0||i+delta>=a.length)return;if(a[i]!.lock==='fixed'){notify('请先解除固定时间，再移动安排');return}const [item]=a.splice(i,1);a.splice(i+delta,0,item!);commit(n,'调整地点顺序')}
function drop(target:string){if(!dragging.value||target===dragging.value)return;const a=trip.value.days[dayIndex.value]!.activities;const from=a.findIndex(x=>x.id===dragging.value),to=a.findIndex(x=>x.id===target);move(dragging.value,to-from);dragging.value=''}
function removeActivity(id:string){const n=clone(trip.value);const a=n.days[dayIndex.value]!.activities.find(a=>a.id===id)!;if(a.lock!=='none'){notify('该安排已锁定，请先解除锁定');return}n.days[dayIndex.value]!.activities=n.days[dayIndex.value]!.activities.filter(a=>a.id!==id);commit(n,'移除活动');modal.value=''}
function edit(id:string){const a=trip.value.days[dayIndex.value]!.activities.find(a=>a.id===id)!;editActivity.value=clone(a);editDuration.value=a.duration;editDate.value=dayIndex.value;openModal('edit')}
function saveEdit(){if(!editActivity.value)return;const a=editActivity.value;if(a.lock==='fixed'&&(editDuration.value!==a.duration||editDate.value!==dayIndex.value)){notify('固定安排不能更改时间，请先解除锁定');return}const n=clone(trip.value);const index=n.days[dayIndex.value]!.activities.findIndex(x=>x.id===a.id);n.days[dayIndex.value]!.activities.splice(index,1);a.duration=Number(editDuration.value);if(editDate.value===dayIndex.value)n.days[dayIndex.value]!.activities.splice(index,0,a);else n.days[editDate.value]!.activities.push(a);commit(n,'编辑活动安排');modal.value=''}
async function addPlace(id:string){
 if(busy.value)return
 const place=placeById(id);if(!place)return
 visitSearch.value={query:place.name,places:[place],tripId:trip.value.id,revision:trip.value.revision,day:dayIndex.value}
 modal.value='';page.value='canvas';tabMobile.value='ai'
 await chooseVisit(id)
}
function assertCurrent(before:Trip,day:number){if(trip.value.id!==before.id||trip.value.revision!==before.revision||dayIndex.value!==day)throw new Error('行程或日期已变化，请重新搜索并规划')}
async function lookupVisitPlaces(query:string,before:Trip,day:number){
 const q=normalizePlaceQuery(query);if(q.length<2||q.length>80)throw new Error('请输入 2—80 个字的地点或校区名称')
 busyText.value=`正在搜索 ${q}…`
 const data=await api('/places/search?q='+encodeURIComponent(q));assertCurrent(before,day)
 registerPlaces(data.places)
 const matches:Place[]=data.places.map((p:Place)=>places.find(seed=>!!seed.coordinateSource&&seed.coordinateSource===p.coordinateSource)||p)
 const unique=matches.filter((p,i)=>matches.findIndex(other=>other.id===p.id)===i)
 visitSearch.value={query:q,places:unique,tripId:before.id,revision:before.revision,day};visitLookupQuery.value=q
 response.value=unique.length?`找到 ${unique.length} 个相关地点，请按校区和地址确认后重新规划。`:'没有找到匹配地点，当前覆盖杭州周边；可换用完整地点或校区名称。'
}
async function refineVisitSearch(){if(busy.value)return;busy.value=true;response.value='';try{await lookupVisitPlaces(visitLookupQuery.value,clone(trip.value),dayIndex.value)}catch(e){response.value=(e as Error).message}finally{busy.value=false}}
async function chooseVisit(id:string){
 const context=visitSearch.value;if(!context||busy.value)return
 if(context.tripId!==trip.value.id||context.revision!==trip.value.revision||context.day!==dayIndex.value){visitSearch.value=null;notify('行程已变化，请重新搜索');return}
 const place=context.places.find(p=>p.id===id);if(!place)return
 const before=clone(trip.value),day=context.day
 busy.value=true;busyText.value='正在把选中地点加入路线并核验道路…';proposal.value=null;response.value='';proposalRouteError.value='';proposalRoutes.value={...roadRoutes.value}
 try{let next=visitProposal(before,day,place,context.query);if(next.trip.days[day]!.activities.length>1)proposalRoutes.value[routeKey(next.trip,day)]=await loadRoute(next.trip,day);next=await coordinateProposal(before,next,day,proposalRoutes.value,loadRoute,text=>busyText.value=text);assertCurrent(before,day);proposal.value=next;visitSearch.value=null;response.value=next.summary}catch(e){response.value=(e as Error).message}finally{busy.value=false}
}
async function generate(text=prompt.value){
 if(busy.value||!text.trim())return
 busy.value=true;busyText.value='正在处理你的需求…';prompt.value=text;submittedPrompt.value=text;proposal.value=null;visitSearch.value=null;response.value='';proposalRouteError.value='';proposalRoutes.value={...roadRoutes.value}
 const before=clone(trip.value),day=dayIndex.value
 try{
  const query=extractVisitQuery(text)
  if(query){await lookupVisitPlaces(query,before,day);return}
  let next:Proposal
  const delay=departureDelay(text)
  if(delay!==null)next=departureProposal(before,day,delay)
  else if(user.value){
   const health=await checkAPI()
   if(!health)throw new Error('暂时无法连接云端，请重新连接后重试。地点搜索和需求会保留。')
   if(!health.model)throw new Error('AI 模型尚未配置；你仍可搜索地点、加入行程和整体顺延。')
   next=await remoteSuggestion(before,day,text)
  }else next=suggest(before,day,text)
  for(let i=0;i<next.trip.days.length;i++){if(JSON.stringify(next.trip.days[i])===JSON.stringify(before.days[i]))continue;if(next.trip.days[i]!.activities.length>1){try{proposalRoutes.value[routeKey(next.trip,i)]=await loadRoute(next.trip,i)}catch(e){proposalRouteError.value='道路用时未核验：'+(e as Error).message}}}
  if(trip.value.id!==before.id||trip.value.revision!==before.revision||dayIndex.value!==day)throw new Error('生成期间行程发生变化，请重新调整')
  if(next.departure)next=departureProposal(before,day,next.departure.delay,proposalRoutes.value)
  if(!proposalRouteError.value)next=await coordinateProposal(before,next,day,proposalRoutes.value,loadRoute,text=>busyText.value=text)
  if(trip.value.id!==before.id||trip.value.revision!==before.revision||dayIndex.value!==day)throw new Error('协调期间行程发生变化，请重新调整')
  proposal.value=next;response.value=next.summary
 }catch(e){if(e instanceof ApiError&&e.status===401)user.value=null;response.value=(e as Error).message}finally{busy.value=false}
}
async function changeDepartureOption(event:Event,key:'shiftFixed'|'extendEnd'){
 const p=proposal.value;if(!p?.departure||busy.value)return
 if(p.baseRevision!==trip.value.revision){notify('行程已变化，请重新生成方案');return}
 const {day,delay,shiftFixed,extendEnd}=p.departure
 const before=clone(trip.value),next=departureProposal(before,day,delay,proposalRoutes.value,{shiftFixed,extendEnd,[key]:(event.target as HTMLInputElement).checked})
 busy.value=true;busyText.value='正在重新协调时间…'
 try{const result=await coordinateProposal(before,next,day,proposalRoutes.value,loadRoute,text=>busyText.value=text);if(trip.value.id!==before.id||trip.value.revision!==before.revision||dayIndex.value!==day)throw new Error('行程已变化，请重新生成');proposal.value=result}catch(e){response.value=(e as Error).message}finally{busy.value=false}
}
function useEarlierStart(){const p=proposal.value;if(!p||p.baseRevision!==trip.value.revision){notify('行程已变化，请重新生成');return}try{const next=earlierProposal(p);const violations=coordinationViolations(trip.value,next,proposalRoutes.value);if(violations.length)throw new Error(violations.join('；'));proposal.value=next;response.value='已预览提前出发的备选方案，原行程尚未修改。'}catch(e){notify((e as Error).message)}}
function applyProposal(){
 if(!proposal.value||busy.value)return
 const p=proposal.value
 if(p.trip.id!==trip.value.id||p.baseRevision!==trip.value.revision){notify('行程已变化，请重新生成方案');return}
 if(p.visit&&!p.trip.days[p.visit.day]?.activities.some(a=>a.placeId===p.visit!.placeId&&a.lock!=='none')){notify('选中的目的地未保留，请重新规划');return}
 const violations=p.coordination?coordinationViolations(trip.value,p,proposalRoutes.value):p.departure?(validDepartureProposal(trip.value,p,proposalRoutes.value)?[]:['顺延方案已失效，请重新生成']):lockedChanges(trip.value,p.trip)
 if(violations.length){notify(violations.join('；'));return}
 if(proposalRouteError.value){notify('请先重新生成，完成道路路线核验');return}
 if(proposalIssues.value.length){notify('方案中仍有时间冲突，请先调整条件');return}
 Object.assign(roadRoutes.value,proposalRoutes.value)
 if(commit(p.trip,'应用智能调整',p.baseRevision)){if(p.visit)selected.value=p.trip.days[p.visit.day]!.activities.find(a=>a.placeId===p.visit!.placeId)!.id;notify(p.visit?'已加入选中地点并更新道路路线，可在历史版本中恢复':'新的安排已应用，可以在历史版本中恢复')}
}
function restore(id:string){const v=store.versions[trip.value.id]?.find(x=>x.id===id);if(!v)return;commit(clone(v.trip),'恢复历史版本');modal.value='';notify('已恢复，并保留所有历史记录')}
function setMode(){const t=clone(trip.value);t.mode=t.mode==='walk'?'drive':'walk';commit(t,'切换交通方式')}
function saveSettings(event:Event,key:'start'|'end'){const v=(event.target as HTMLInputElement).value;const [h,m]=v.split(':').map(Number);const n=clone(trip.value);n.days[dayIndex.value]![key]=h!*60+m!;commit(n,'更新每日时间范围')}
function download(name:string,content:string,type:string){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function exportTrip(){download(trip.value.title+'.json',JSON.stringify(trip.value,null,2),'application/json');notify('行程文件已导出')}
function escapeHTML(v:string){return v.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!))}
function exportCard(){const t=trip.value;const body=t.days.map((d,i)=>`<section><h2>DAY ${i+1} · ${d.date}</h2>${schedule(t,i,roadRoutes.value[routeKey(t,i)]).map(e=>`<article><time>${time(e.start)}</time><div><h3>${escapeHTML(e.place.name)}</h3><p>${e.activity.duration} 分钟 · ${escapeHTML(e.place.kind)}</p></div></article>`).join('')}</section>`).join('');download(t.title+'.html',`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHTML(t.title)}</title><style>body{font:16px system-ui;background:#f5f4ec;color:#224c41;max-width:720px;margin:50px auto;padding:25px}small,p{color:#7a827b}h1{font-size:36px}section{background:white;padding:24px;margin:20px 0;border-radius:18px}article{display:flex;gap:25px;border-top:1px solid #eee;padding:15px 0}h3{margin:0}time{padding-top:3px}h2{font-size:20px}</style><small>拾途杭州 TRIPCANVAS · 旅行卡片</small><h1>${escapeHTML(t.title)}</h1><p>${t.days[0]!.date} · ${t.days.length} 天 · ${t.people} 人</p>${body}<p>旅行计划。已加载日期按道路用时排程，未加载日期仍为估算；不含实时路况，营业时间与价格请在出行前确认。此文件是独立副本，分享后不能远程撤销。</p></html>`,'text/html');notify('旅行卡片已导出，可在浏览器打开')}
async function copySummary(){const text=trip.value.title+'\n'+trip.value.days.map((d,i)=>`${d.date}\n`+schedule(trip.value,i,roadRoutes.value[routeKey(trip.value,i)]).map(e=>`${time(e.start)} ${e.place.name}`).join('\n')).join('\n\n')+'\n\n来自拾途杭州 · 出发前请确认实际信息';try{await navigator.clipboard.writeText(text);notify('行程摘要已复制')}catch{notify('剪贴板不可用，请使用导出旅行卡片')}}
async function createShare(){if(!backend.value||!user.value){notify('可撤销链接需要连接后端并登录；当前可导出旅行卡片');return}shareBusy.value=true;try{await api('/trips/'+trip.value.id,'PUT',{trip:trip.value});const r=await api('/trips/'+trip.value.id+'/shares','POST',{});shareUrl.value=location.origin+'/#share='+r.token;shareId.value=r.id}catch(e){notify((e as Error).message)}finally{shareBusy.value=false}}
async function revokeShare(){try{await api('/shares/'+shareId.value,'DELETE');shareUrl.value='';shareId.value='';notify('链接已撤销')}catch(e){notify((e as Error).message)}}
async function auth(event:Event){
 if(accountBusy.value)return
 accountBusy.value=true;authError.value=''
 try{
  const credentials=credentialsFromForm(new FormData(event.currentTarget as HTMLFormElement))
  const info=await checkAPI(!backend.value)
  if(!info)throw new Error(backendConfigured?'云端服务尚未唤醒，请稍后重新连接。':'云端同步尚未配置，当前行程保存在此设备。')
  const session=await api('/auth/'+(loginMode.value?'register':'login'),'POST',credentials)
  setToken(session.access_token);user.value={username:session.username};username.value=session.username;password.value=''
  // A failed trip sync must not misreport a successful sign-in as a login error.
  let synced=true
  try{
   const remote=await api('/trips')
   for(const t of remote){registerPlaces(t.placeSnapshots);const index=store.trips.findIndex(x=>x.id===t.id);if(index>=0)store.trips[index]=t;else store.trips.push(t)}
  }catch{synced=false}
  modal.value=resumeBuilder.value?'new':'';resumeBuilder.value=false
  notify(synced?'登录成功，可以使用 AI 调整与云端同步':'已登录；云端行程暂未加载，可稍后重新连接')
  await syncTrip(trip.value)
 }catch(e){authError.value=(e as Error).message}finally{accountBusy.value=false}
}
async function logout(){try{await api('/auth/logout','POST')}catch{}setToken('');user.value=null;modal.value='';notify('已退出账号，本地行程仍保留')}
async function reconnect(){const info=await checkAPI(true);if(info){try{user.value=await api('/auth/me')}catch{user.value=null}notify('云端服务已连接')}}
watch(()=>trip.value.id,()=>{dayIndex.value=0;proposal.value=null})
watch(()=>entries.value.map(x=>x.activity.id).join(','),()=>{if(!entries.value.some(e=>e.activity.id===selected.value))selected.value=entries.value[0]?.activity.id||''})
onMounted(async()=>{selected.value=entries.value[0]?.activity.id||'';const info=await checkAPI();try{if(backend.value)user.value=await api('/auth/me')}catch{}if(location.hash.startsWith('#share=')){try{const shared=await api('/public/shares/'+encodeURIComponent(location.hash.slice(7)));registerPlaces(shared.placeSnapshots);shareTrip.value=shared}catch{notify('分享链接已失效，或无法连接后端服务')}}})
</script>
<template>
 <div class="app-shell">
  <header class="site-header">
   <button class="brand" @click="page='canvas'" aria-label="拾途杭州首页"><img src="/favicon.svg" alt=""/><span>拾途杭州<small>TRIPCANVAS · HANGZHOU</small></span></button>
   <nav class="primary-nav" aria-label="主要导航"><button :class="{active:page==='canvas'}" @click="page='canvas'"><Map :size="17"/>旅行画布</button><button :class="{active:page==='trips'}" @click="page='trips'"><Compass :size="17"/>我的旅行</button><button :class="{active:page==='favorites'}" @click="page='favorites'"><Heart :size="17"/>灵感收藏</button></nav>
   <div class="header-actions"><span class="local-mode"><i/>{{user?'已登录':connection.status==='waking'?'连接中':backend?'云端已连接':'本地体验'}}</span><button class="icon-button help-button" @click="openModal('help')" aria-label="使用帮助"><Info :size="19"/></button><button class="avatar" @click="openModal('account')" aria-label="账号设置">{{user?.username.slice(0,1).toUpperCase()||'旅'}}</button></div>
  </header>
  <div v-if="backendConfigured&&['checking','waking','offline'].includes(connection.status)" class="connection-banner" role="status"><LoaderCircle v-if="connection.status!=='offline'" :size="16" class="spin"/><Info v-else :size="16"/><span>{{connection.status==='offline'?'云端暂未连接，本地行程仍然保留。':'正在唤醒云端服务，首次连接可能需要约一分钟。你可以先编辑行程。'}}</span><button v-if="connection.status==='offline'" @click="reconnect">重新连接</button></div>
  <main v-if="shareTrip" class="public-page"><span class="eyebrow">A LITTLE JOURNEY, SHARED WITH YOU</span><h1>{{shareTrip.title}}</h1><p>一份来自拾途杭州的旅行计划</p><section v-for="(d,i) in shareTrip.days" :key="d.date"><h2>DAY {{i+1}} · {{shortDate(d.date)}}</h2><div v-for="e in schedule(shareTrip,i)" :key="e.activity.id" class="shared-row"><span>{{time(e.start)}}</span><strong>{{e.place.name}}</strong><small>{{e.activity.duration}} 分钟</small></div></section><button class="button primary" @click="closeShare()">开始我的旅行<ArrowRight :size="17"/></button></main>
  <main v-else-if="page==='canvas'" class="canvas-page">
   <div class="breadcrumb"><button @click="page='trips'">我的旅行</button><ChevronRight :size="13"/><span>{{trip.title}}</span><span class="example-badge">{{trip.requirements?'已确认需求':'示例行程'}}</span></div>
   <section class="trip-heading"><div><div class="heading-overline">HANGZHOU · 1–3 DAY JOURNEYS</div><h1>{{trip.title}}<button class="title-edit" @click="openModal('trip-settings')" aria-label="编辑旅行信息"><Settings2 :size="17"/></button></h1><div class="trip-meta"><span><CalendarDays :size="15"/>{{daysLabel}}</span><i/><span><Users :size="15"/>{{trip.people}} 人同行</span><i/><span><Leaf :size="15"/>慢游 · 留一点空白</span></div></div><div class="trip-heading-actions"><span class="save-state"><CheckCircle2 :size="14"/>{{store.storageError?'存储空间不足':savedAt}}</span><div><button class="button quiet" @click="openModal('history')"><History :size="16"/>历史版本</button><button class="button primary" @click="openModal('share')"><Share2 :size="16"/>分享行程</button></div></div></section>
   <div class="mobile-tabs"><button :class="{active:tabMobile==='plan'}" @click="tabMobile='plan'">每日行程</button><button :class="{active:tabMobile==='map'}" @click="tabMobile='map'">路线地图</button><button :class="{active:tabMobile==='ai'}" @click="tabMobile='ai'">智能调整</button></div>
   <section class="workspace">
    <aside class="itinerary-panel" :class="{'mobile-show':tabMobile==='plan'}">
     <div class="panel-heading"><h2>每日行程 <span>{{trip.days.length}} DAYS</span></h2><div class="plan-heading-actions"><button class="icon-button" @click="openModal('search')" :disabled="busy" aria-label="搜索地点并加入当天路线" title="添加想去的地点"><Plus :size="18"/></button><button class="icon-button" @click="openModal('trip-settings')" aria-label="行程设置"><Settings2 :size="17"/></button></div></div>
     <div class="day-tabs"><button v-for="(d,i) in trip.days" :key="d.date" :class="{active:dayIndex===i}" @click="changeDay(i)"><strong>Day {{i+1}}</strong><span>{{shortDate(d.date)}} · {{weekday(d.date)}}</span></button></div>
     <div class="day-overview"><div><span class="tiny-dot"/>{{dayIndex===0?'沿着西湖，慢慢走':dayIndex===1?'南岸的人文与树影':'留给喜欢的风景'}}</div><button @click="setMode" :title="'切换交通方式'"><Footprints v-if="trip.mode==='walk'" :size="14"/><Car v-else :size="14"/>{{trip.mode==='walk'?'步行':'驾车'}}<ChevronDown :size="12"/></button></div>
     <div class="timeline-scroll">
      <div v-if="!entries.length" class="empty-state"><MapPin :size="32"/><p>今天，留给新的发现。</p><button @click="openModal('search')" class="button quiet">添加一个地点</button></div>
      <template v-for="(entry,index) in entries" :key="entry.activity.id">
       <div v-if="index" class="travel-gap"><span class="dotted-line"/><Footprints v-if="trip.mode==='walk'" :size="12"/><Car v-else :size="12"/><span>{{routeReady?'道路':'估算'}} {{entry.travel}} 分钟 · {{entry.distance.toFixed(1)}} km</span></div>
       <article class="activity-card" :class="{selected:selected===entry.activity.id,conflict:entry.conflict}" draggable="true" @dragstart="dragging=entry.activity.id" @dragover.prevent @drop.prevent="drop(entry.activity.id)" @click="selected=entry.activity.id">
        <div class="activity-top"><span class="activity-number">{{String(index+1).padStart(2,'0')}}</span><span class="activity-time">{{time(entry.start)}} <span>— {{time(entry.end)}}</span></span><button class="icon-button lock-button" :class="{locked:entry.activity.lock!=='none'}" @click.stop="toggleLock(entry.activity.id)" :aria-label="`${entry.activity.lock==='none'?'锁定':'更改锁定'}${entry.place.name}`" :title="entry.activity.lock==='none'?'点击设为必须保留':entry.activity.lock==='keep'?'必须保留 · 点击固定时间':'固定安排 · 点击解锁'"><LockKeyhole v-if="entry.activity.lock!=='none'" :size="13"/><LockKeyholeOpen v-else :size="13"/></button></div>
        <div class="activity-content"><img :src="imageFor(entry.place.image)" alt="旅行氛围配图" loading="lazy"/><div><h3>{{entry.place.name}}</h3><p>{{entry.place.tag}}</p><div class="activity-tags"><span>{{entry.place.kind}}</span><span v-if="entry.activity.lock!=='none'" class="lock-tag">{{entry.activity.lock==='fixed'?'固定安排':'必须保留'}}</span></div></div></div>
        <div class="activity-bottom"><span><Clock :size="12"/>停留 {{entry.activity.duration}} 分钟</span><div><button @click.stop="move(entry.activity.id,-1)" :disabled="index===0" aria-label="上移活动"><ArrowUp :size="13"/></button><button @click.stop="move(entry.activity.id,1)" :disabled="index===entries.length-1" aria-label="下移活动"><ArrowDown :size="13"/></button><button @click.stop="edit(entry.activity.id)" aria-label="编辑活动"><MoreHorizontal :size="17"/></button></div></div>
        <span v-if="entry.conflict" class="conflict-text"><AlertTriangle :size="12"/>时间安排有冲突</span>
       </article>
      </template>
      <button class="add-stop" @click="openModal('search')"><Plus :size="16"/>添加一个想去的地方</button>
      <p class="timeline-footnote"><GripVertical :size="12"/>拖动调整顺序 · {{routeReady?'道路用时不含实时路况':'道路用时待核验，当前为估算'}}</p>
     </div>
     <button class="check-strip" :class="{warning:errors.length}" @click="openModal('checks')"><ShieldCheck v-if="!errors.length" :size="17"/><AlertTriangle v-else :size="17"/><span>{{errors.length?`${errors.length} 处时间安排需要留意`:(checkedDays===trip.days.length?'已按道路用时检查':'时间检查 · 部分路段待核验')}}<small>{{errors.length?'查看并调整行程':'营业时间与实际路况待确认'}}</small></span><ChevronRight :size="15"/></button>
    </aside>
    <section class="map-panel" :class="{'mobile-show':tabMobile==='map'}">
     <TripMap :entries="entries" :selected="selected" :state="routeState" :mode="trip.mode" @select="selected=$event" @retry="refreshRoute"/>
     <div v-if="selectedEntry" class="map-place-card"><img :src="imageFor(selectedEntry.place.image)" alt="旅行氛围配图"/><div><div class="map-place-label">这一站 · {{selectedEntry.place.kind}}</div><h3>{{selectedEntry.place.name}}</h3><p>{{selectedEntry.place.tag}}<span> · {{selectedEntry.activity.duration}} 分钟</span></p></div><button class="icon-button" @click="edit(selectedEntry.activity.id)" aria-label="查看地点详情"><ArrowUpRight :size="19"/></button></div>
     <div class="map-stats"><div><span><MapPin :size="14"/>今日地点</span><strong>{{entries.length}}<small> 个</small></strong></div><div><span><Footprints :size="14"/>{{routeReady?'道路里程':'估算里程'}}</span><strong>{{distance.toFixed(1)}}<small> km</small></strong></div><div><span><Clock :size="14"/>{{routeReady?'道路用时':'估算用时'}}</span><strong>{{totalTravel}}<small> 分钟</small></strong></div><div><span>个人预算</span><strong><small>¥ </small>{{budget}}</strong></div></div>
     <div class="map-route-note">{{routeReady?'道路时间不含实时交通、停车与进出景区。':'路线未核验，当前时间与里程仅为粗略估算。'}} <span v-if="entries.some(e=>e.place.costKnown===false)">部分地点费用未知，预算仅汇总已知金额。</span><span v-if="routeState.route?.waypoints.some(w=>w.distance>100)">虚线连接地点与附近道路。</span></div>
    </section>
    <aside class="assistant-panel" :class="{'mobile-show':tabMobile==='ai'}">
     <div class="assistant-header"><span class="spark-icon"><Sparkles :size="19"/></span><div><h2>你的旅行搭子</h2><p>一起，把旅程调整到刚刚好。</p></div><span class="ai-badge">{{user?(hasModel?'AI 已连接':'路线助手'):'未登录'}}</span></div>
     <div class="assistant-tools"><button type="button" :disabled="busy" @click="openModal('search')"><Plus :size="14"/>添加想去的地点</button><button v-if="!user" type="button" @click="openModal('account')">登录使用 AI<ArrowRight :size="12"/></button><span v-else>已登录 · {{hasModel?'AI 调整可用':'地点规划可用'}}</span></div>
     <div ref="assistantScroll" class="assistant-scroll">
      <div class="assistant-intro"><span class="mini-eyebrow">HELLO, TRAVELER</span><h3>好旅行，<br>也需要一点留白。</h3><p>想晚点出发，还是多坐一会儿？<br>告诉我，我们一起调整。</p><div class="intro-art"><div class="sun-disc"/><div class="hill hill-back"/><div class="hill hill-front"/><div class="art-path"/><span class="tiny-tree">♧</span><div class="art-note">TAKE IT SLOW</div></div></div>
      <div class="suggestion-title">试试这样调整 <Sparkles :size="13"/></div>
      <div class="quick-prompts"><button @click="generate('今天轻松一点，减少一个地点')" :disabled="busy"><Leaf :size="16"/><span>今天想轻松一点</span><ArrowUpRight :size="14"/></button><button @click="generate('下雨了，改成室内活动')" :disabled="busy"><CloudRain :size="16"/><span>换成室内的好去处</span><ArrowUpRight :size="14"/></button><button @click="generate('晚出发一小时')" :disabled="busy"><Coffee :size="16"/><span>多睡一会，晚点出发</span><ArrowUpRight :size="14"/></button></div>
      <div class="protected-note"><LockKeyhole :size="14"/><p>你锁定的安排，会好好保留。<span>调整前可以预览，确认后再应用。</span></p></div>
      <div v-if="submittedPrompt" class="submitted-message"><small>你的需求 · Day {{dayIndex+1}}</small><p>{{submittedPrompt}}</p></div>
      <div v-if="busy" class="assistant-message" role="status"><LoaderCircle :size="17" class="spin"/>{{busyText}}</div>
      <div v-if="response" class="assistant-message response" role="status"><Sparkles :size="16"/><p>{{response}}</p></div>
      <section v-if="visitSearch" class="visit-search-card" aria-label="确认想去的具体地点"><h3>选择具体地点，再规划。</h3><p>加入 Day {{visitSearch.day+1}}，核验路线并协调时间。选中的地点会设为必须保留。</p><form class="visit-refine" @submit.prevent="refineVisitSearch"><input v-model="visitLookupQuery" :readonly="busy" aria-label="地点或校区名称" maxlength="80"/><button type="submit" class="button quiet small" :disabled="busy||visitLookupQuery.trim().length<2">重新搜索</button></form><article v-for="p in visitSearch.places" :key="p.id"><strong>{{p.name}}</strong><p>{{p.address||p.tag}}</p><a v-if="p.coordinateSource" :href="p.coordinateSource" target="_blank" rel="noopener noreferrer">核对地图位置<ExternalLink :size="11"/></a><button class="button primary small full" :disabled="busy" :aria-label="`规划到${p.name}`" @click="chooseVisit(p.id)">选此地点并重新规划<ArrowRight :size="13"/></button></article><p v-if="!visitSearch.places.length">没有匹配结果，请输入完整地点或校区名称。</p><small>© OpenStreetMap contributors · Photon 真实地点搜索</small><button class="text-button full" @click="visitSearch=null;response=''" :disabled="busy">暂不添加</button></section>
      <div v-if="proposal" class="proposal-card"><div class="proposal-head"><span>{{proposal.visit?'新地点路线预览':proposal.departure?'整体顺延预览':'调整预览'}}</span><small>{{proposal.source==='schedule'?'排程计算 · ':''}}Day {{dayIndex+1}}</small></div>
       <div v-if="visitEntry" class="visit-confirmed"><strong>{{visitEntry.place.name}}</strong><span>Day {{proposal.visit!.day+1}} · {{time(visitEntry.start)}}—{{time(visitEntry.end)}} · 必须保留</span><small>已按{{proposal.trip.mode==='walk'?'步行':'驾车'}}道路路线规划；确认后更新画布。</small></div>
       <div v-if="proposal.departure" class="departure-options"><label v-if="canShiftFixed"><input type="checkbox" :disabled="busy" :checked="proposal.departure.shiftFixed" @change="changeDepartureOption($event,'shiftFixed')"/>固定游览安排也顺延</label><label><input type="checkbox" :disabled="busy" :checked="proposal.departure.extendEnd" @change="changeDepartureOption($event,'extendEnd')"/>允许当天结束时间延后</label><small>优先保留地点与顺序；必要时协调未锁定停留。勾选后先预览，返程期限保持。</small></div>
       <div v-if="proposal.coordination" class="coordination-note" :class="{unresolved:!proposal.coordination.resolved}"><strong>{{proposal.coordination.resolved?'已自动协调时间':'自动协调后仍有冲突'}}</strong><span>{{proposal.coordination.resolved?'已核验道路时间并保留锁定安排，具体变化如下。':'当前固定条件下尚未找到可行安排，可选择下方备选出发时间，或调整锁定条件。'}}</span><span v-for="warning in proposal.coordination.warnings" :key="warning">{{warning}}</span></div>
       <p v-for="(change,i) in proposal.changes" :key="i"><ArrowRight :size="14"/>{{change}}</p>
       <details v-if="departureRows.length" :open="!!proposal.departure" class="time-comparison"><summary>逐站时间对比</summary><div class="departure-timeline" aria-label="逐站时间对比"><div class="departure-row departure-row-head"><span>地点</span><span>原安排</span><span>新安排</span></div><div v-for="row in departureRows" :key="row.id" class="departure-row"><strong>{{row.name}}</strong><span v-if="row.before">{{time(row.before.start)}}<small>— {{time(row.before.end)}}</small></span><span v-else>新增</span><span v-if="row.after" :class="{late:row.after.conflict}">{{time(row.after.start)}}<small>— {{time(row.after.end)}}</small></span><span v-else>本次移除</span></div></div></details>
       <div v-for="(issue,i) in proposalIssues" :key="`${issue.id}-${i}`" class="proposal-error"><AlertTriangle :size="14"/>{{issue.message}}</div><button v-if="proposal.coordination?.earlierStart!==undefined&&!proposal.coordination.resolved" class="button quiet full earlier-option" :disabled="busy" @click="useEarlierStart">改为 {{time(proposal.coordination.earlierStart)}} 出发并预览</button><p v-if="proposalRouteError" class="proposal-error">{{proposalRouteError}}，请重新生成。</p><button class="button primary" @click="applyProposal" :disabled="busy||proposalIssues.length>0||!!proposalRouteError"><Check :size="16"/>{{proposal.departure?'确认顺延并应用':'应用这个安排'}}</button><button class="text-button" @click="proposal=null;response=''">保留原计划</button></div>
     </div>
     <form class="composer" @submit.prevent="submitComposer"><textarea ref="composerInput" v-model="prompt" rows="2" placeholder="杭州想去哪？例如：我想参观浙工大" aria-label="行程调整需求" aria-describedby="composer-help" maxlength="1000" :readonly="busy" :aria-busy="busy" @input="syncComposerValue" @keydown="onPromptKeydown" @compositionend="syncComposerValue"/><p id="composer-help" class="composer-help">选词后 Enter 发送 · Shift+Enter 换行</p><div><span><Sparkles :size="12"/>仅调整 Day {{dayIndex+1}}</span><button type="submit" :disabled="busy||!prompt.trim()" aria-label="发送调整需求"><LoaderCircle v-if="busy" class="spin" :size="16"/><ArrowUp v-else :size="17"/></button></div></form>
     <p class="assistant-disclaimer">{{proposal?.source==='schedule'?'本次为地点与道路排程计算，确认后才应用':hasModel&&user?'建议仍需结合实际情况确认':'可搜索地点并规划路线；AI 自由调整需登录'}}</p>
    </aside>
   </section>
   <footer class="workspace-footer"><span><Leaf :size="13"/>在杭州，把时间留给喜欢的风景。</span><button @click="openModal('help')">示例数据与图片说明<ArrowUpRight :size="12"/></button></footer>
  </main>
  <main v-else-if="page==='trips'" class="collection-page"><div class="collection-heading"><div><span class="eyebrow">YOUR NEXT LITTLE ESCAPE</span><h1>在杭州，走自己喜欢的路线。</h1><p>从想去的杭州地点出发，安排自己的 1—3 日行程。</p></div><button class="button primary" @click="openModal('new')"><Plus :size="17"/>开启新旅行</button></div><div class="trip-grid"><article v-for="t in store.trips" :key="t.id" class="trip-tile"><button class="trip-cover" @click="openTrip(t.id)"><img src="/assets/travel.jpg" alt="山水旅行氛围图"/><span>{{t.city}} · {{t.days.length}} DAYS</span><ArrowUpRight :size="22"/></button><div class="trip-tile-info"><h2 @click="openTrip(t.id)">{{t.title}}</h2><p><CalendarDays :size="14"/>{{shortDate(t.days[0]!.date)}}<span>· {{t.people}} 人</span><button class="icon-button" @click="deletingTrip=t.id;openModal('delete')" aria-label="删除旅行"><Trash2 :size="15"/></button></p></div></article><button class="new-trip-tile" @click="openModal('new')"><span><Plus :size="26"/></span><strong>杭州，下一站想去哪？</strong><p>从一个小小的出发念头开始</p></button></div></main>
  <main v-else class="collection-page"><div class="collection-heading"><div><span class="eyebrow">PLACES THAT SPEAK TO YOU</span><h1>先收藏，再出发。</h1><p>把心动的地点，留给下一次旅行。</p></div><button class="button quiet" @click="openModal('search')"><Search :size="17"/>发现地点</button></div><div class="favorites-grid"><article v-for="p in places.filter(p=>store.favorites.includes(p.id))" :key="p.id" class="favorite-card"><div><img :src="imageFor(p.image)" alt="旅行氛围配图"/><button class="favorite-heart" @click="store.toggleFavorite(p.id)" aria-label="取消收藏"><Heart :size="18" fill="currentColor"/></button></div><section><span class="tag">{{p.kind}}</span><h2>{{p.name}}</h2><p>{{p.description}}</p><button class="button quiet" @click="addPlace(p.id);page='canvas'"><Plus :size="16"/>加入当前行程</button></section></article></div><div v-if="!store.favorites.length" class="empty-state"><Heart :size="32"/><p>还没有收藏，去发现一个喜欢的地方吧。</p></div></main>
  <Transition name="toast"><div v-if="toast" class="toast-message" role="status"><CheckCircle2 :size="17"/>{{toast}}</div></Transition>
  <div v-if="modal&&modal!=='new'" class="modal-backdrop" @click.self="modal=''" @keydown.esc="modal=''">
   <section class="modal" :class="{'wide-modal':modal==='search'}" role="dialog" aria-modal="true" :aria-label="({delete:'删除旅行',new:'开启新旅行',search:'添加地点',history:'历史版本',share:'分享行程',edit:'编辑活动',checks:'行程检查',account:'账号设置',help:'关于拾途','trip-settings':'旅行设置'} as Record<string,string>)[modal]">
    <button class="modal-close icon-button" @click="modal=''" aria-label="关闭弹窗"><X :size="20"/></button>
    <template v-if="modal==='delete'"><span class="eyebrow">YOUR TRAVEL COLLECTION</span><h2>删除这份旅行？</h2><p class="modal-description">该旅行的历史版本和关联分享链接也会删除。需要保留时，请先导出完整计划。</p><div class="modal-actions"><button class="button quiet" @click="modal=''">保留旅行</button><button class="button danger" @click="deleteTrip">确认删除</button></div></template><template v-else-if="modal==='search'"><span class="eyebrow">MAKE ROOM FOR A LITTLE DISCOVERY</span><h2>加一个想去的地方</h2><p class="modal-description">加入 Day {{dayIndex+1}}。选择具体地点后，会重算道路与时间；确认预览后才保存。</p><PlacePicker :favorites="store.favorites" @favorite="store.toggleFavorite" :selected="trip.days[dayIndex]!.activities.map(a=>a.placeId)" @choose="addPlace($event.id)"/></template>
    <template v-else-if="modal==='edit'&&editActivity"><span class="eyebrow">A MOMENT IN YOUR JOURNEY</span><h2>{{placeById(editActivity.placeId).name}}</h2><img class="detail-image" :src="imageFor(placeById(editActivity.placeId).image)" alt="旅行氛围配图"/><p class="modal-description">{{placeById(editActivity.placeId).description}}</p><a class="route-detail-link" :href="`https://www.openstreetmap.org/?mlat=${placeById(editActivity.placeId).lat}&mlon=${placeById(editActivity.placeId).lng}#map=17/${placeById(editActivity.placeId).lat}/${placeById(editActivity.placeId).lng}`" target="_blank" rel="noopener noreferrer">在 OpenStreetMap 查看位置<ExternalLink :size="13"/></a><div class="form-row"><label>停留时长<select v-model="editDuration"><option v-for="m in [30,45,60,75,80,90,100,110,120,150,180]" :value="m" :key="m">{{m}} 分钟</option></select></label><label>安排日期<select v-model="editDate"><option v-for="(d,i) in trip.days" :key="d.date" :value="i">Day {{i+1}} · {{shortDate(d.date)}}</option></select></label></div><p v-if="editActivity.lock!=='none'" class="form-hint"><LockKeyhole :size="13"/>{{editActivity.lock==='fixed'?'已固定地点和时间，请先在卡片上解锁再更改。':'该地点已设为必须保留。'}}</p><div class="modal-actions"><button class="button danger" @click="removeActivity(editActivity.id)"><Trash2 :size="16"/>移除地点</button><button class="button primary" @click="saveEdit">保存安排<Check :size="16"/></button></div></template>
    <template v-else-if="modal==='history'"><span class="eyebrow">EVERY CHANGE, KEPT SAFE</span><h2>旅行的不同版本</h2><p class="modal-description">放心尝试，喜欢的安排随时可以找回来。</p><div class="version-list"><article v-for="(v,i) in store.versions[trip.id]" :key="v.id"><span class="version-dot"/><div><h3>{{v.label}}<span v-if="i===0">当前版本</span></h3><p>{{new Date(v.date).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}} · {{v.trip.days.reduce((s,d)=>s+d.activities.length,0)}} 个地点</p></div><button v-if="i" class="button quiet small" @click="restore(v.id)"><RotateCcw :size="13"/>恢复</button></article></div></template>
    <template v-else-if="modal==='share'"><span class="eyebrow">GOOD JOURNEYS ARE BETTER SHARED</span><h2>把期待，分享出去。</h2><p class="modal-description">分享当前版本，或者带一份旅行卡片出发。</p><div class="share-preview"><Compass :size="30"/><div><strong>{{trip.title}}</strong><p>{{daysLabel}} · {{trip.days.length}} 天 · {{trip.people}} 人</p></div></div><button class="share-option" @click="exportCard"><Download :size="20"/><span><strong>导出旅行卡片</strong><small>可独立打开的 HTML 文件，副本无法远程撤销</small></span><ChevronRight :size="18"/></button><button class="share-option" @click="copySummary"><Copy :size="20"/><span><strong>复制行程摘要</strong><small>发送给朋友，一起期待下一站</small></span><ChevronRight :size="18"/></button><button class="share-option" @click="exportTrip"><Download :size="20"/><span><strong>导出完整计划</strong><small>JSON 格式，保留活动和锁定条件</small></span><ChevronRight :size="18"/></button><div class="link-share"><button class="button primary full" @click="createShare" :disabled="shareBusy"><Share2 :size="16"/>{{shareBusy?'正在创建…':'创建可撤销分享链接'}}</button><p v-if="!backend||!user" class="form-hint">链接分享需登录配套后端，当前可使用上方导出功能。</p><div v-if="shareUrl" class="share-link"><input :value="shareUrl" readonly aria-label="分享链接"/><button class="text-button" @click="revokeShare">撤销链接</button></div></div></template>
    <template v-else-if="modal==='checks'"><span class="eyebrow">A LITTLE CHECK BEFORE YOU GO</span><h2>让行程更从容</h2><div v-if="!errors.length" class="check-success"><CheckCircle2 :size="23"/><div><strong>目前没有时间冲突</strong><p>{{checkedDays===trip.days.length?'全部日期已按道路用时检查，实际路况仍需确认。':'部分日期仍按估算检查，请切换日期加载道路路线。'}}</p></div></div><div v-for="issue in allIssues" :key="issue.id" class="issue-item"><AlertTriangle :size="18"/><span>{{issue.message}}</span></div><div class="info-box"><Info :size="18"/><p>OpenStreetMap 提供底图，FOSSGIS / OSRM 提供步行与驾车路线。道路用时不含实时交通、停车、排队；地点可能吸附到附近道路。营业时间、票价与预约仍需确认。</p></div></template>
    <template v-else-if="modal==='trip-settings'"><span class="eyebrow">MAKE IT YOUR OWN</span><h2>旅行的小设定</h2><label>旅行名称<input :value="trip.title" @change="commit({...clone(trip),title:($event.target as HTMLInputElement).value.slice(0,50)||trip.title},'修改旅行名称')" maxlength="50"/></label><div class="form-row"><label>Day {{dayIndex+1}} 出发时间<input type="time" :value="time(trip.days[dayIndex]!.start)" @change="saveSettings($event,'start')"/></label><label>当天结束时间<input type="time" :value="time(trip.days[dayIndex]!.end)" @change="saveSettings($event,'end')"/></label></div><label>交通方式<button class="button quiet" @click="setMode"><Footprints v-if="trip.mode==='walk'" :size="16"/><Car v-else :size="16"/>{{trip.mode==='walk'?'步行 · 点击切换驾车':'驾车 · 点击切换步行'}}</button></label><p class="form-hint">变更立即保存。请留意调整后出现的时间冲突。</p><button class="button primary full" @click="modal=''">完成<Check :size="16"/></button></template>
    <template v-else-if="modal==='account'"><span class="eyebrow">YOUR JOURNEYS, YOUR SPACE</span><h2>{{user?'你好，'+user.username:loginMode?'创建拾途杭州账号':'欢迎回来'}}</h2><template v-if="user"><p class="modal-description">行程会同步到你连接的后端，当前设备也保留本地副本。</p><button class="button quiet full" @click="logout">退出登录</button></template><form v-else @submit.prevent="auth" novalidate><p class="modal-description">{{backend?'登录后同步旅行、创建可撤销分享。':backendConfigured?'云端服务可能正在休眠，连接成功后即可登录。':'当前为无需账号的本地体验，行程保存在此浏览器。'}}</p><label>用户名<input v-model="username" name="username" autocomplete="username" :readonly="accountBusy" minlength="3" maxlength="32" required/></label><label>密码<input v-model="password" name="password" :readonly="accountBusy" type="password" :autocomplete="loginMode?'new-password':'current-password'" minlength="8" maxlength="128" required/></label><p class="form-hint">用户名 3—32 个字符；密码至少 8 个字符。</p><p v-if="authError" class="form-error" role="alert">{{authError}}</p><button class="button primary full" :disabled="accountBusy" type="submit">{{accountBusy?'正在连接…':loginMode?'注册并登录':'登录'}}<ArrowRight :size="17"/></button><button type="button" class="text-button full" :disabled="accountBusy" @click="loginMode=!loginMode;authError=''">{{loginMode?'已有账号，去登录':'创建新账号'}}</button></form></template>
    <template v-else-if="modal==='help'"><span class="eyebrow">A THOUGHTFUL WAY TO WANDER</span><h2>你好，这里是拾途杭州。</h2><p class="modal-description">专注杭州 1—3 日短途游，让想去的地点落到地图与时间轴上。</p><div class="help-list"><p><Map :size="20"/><span><strong>在地图上安排旅程</strong>点击地点、拖动行程卡片，或使用上下箭头调整顺序。</span></p><p><LockKeyhole :size="20"/><span><strong>留住已经确定的安排</strong>锁定按钮依次切换：必须保留、固定时间、解除锁定。</span></p><p><Sparkles :size="20"/><span><strong>先预览，再决定</strong>可以输入想去的杭州地点、调整停留与出发时间；核对路线与变化后再确认。</span></p></div><div class="info-box"><Info :size="18"/><p>地图和道路路线已接入 OpenStreetMap 与 FOSSGIS / OSRM，路线最多缓存 24 小时，不含实时路况；地点坐标会发送给路线服务。图片为 Unsplash 氛围素材。计划仍保留本地副本；云端同步与真实模型取决于后端配置。</p></div><button class="button primary full" @click="modal=''">开始慢慢探索<ArrowRight :size="17"/></button></template>
   </section>
  </div>
  <div v-show="modal==='new'" class="modal-backdrop" @click.self="modal=''" @keydown.esc="modal=''"><section class="modal wide-modal" role="dialog" aria-modal="true" aria-label="新建旅行需求与草案"><button class="modal-close icon-button" @click="modal=''" aria-label="关闭弹窗"><X :size="20"/></button><TripBuilder :key="builderKey" :logged-in="!!user" :has-model="hasModel" @created="acceptDraft" @login="resumeBuilder=true;openModal('account')"/></section></div>
 </div>
</template>

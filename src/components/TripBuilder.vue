<script setup lang="ts">
import {ref,computed,watch,onMounted} from 'vue'
import {Check,ArrowRight,ArrowLeft,LoaderCircle,Trash2,MapPin,AlertTriangle,Sparkles} from '@lucide/vue'
import PlacePicker from './PlacePicker.vue'
import AgentTrace from './AgentTrace.vue'
import GuideImporter from './GuideImporter.vue'
import PlanComparison from './PlanComparison.vue'
import type {PlanComparison as Comparison} from '../planningOptions'
import {clone,places,placeById,registerPlaces,time,schedule,type Requirements,type Trip,type Place} from '../domain'
import {api,confirmTrip,beginConversation} from '../api'
import {walkingKm,type RoadRoute} from '../routes'
import {ApiError} from '../apiError'
import {candidateSelection,mentionKey,mergeImportedOptional} from '../candidateSelection'
const props=defineProps<{loggedIn:boolean;hasModel:boolean}>()
const emit=defineEmits<{created:[trip:Trip];login:[]}>()
type Role='visit'|'destination'|'origin'
interface Mention {text:string;role:Role;candidates:Place[];error?:string}
interface Parsed {requirements:Partial<Requirements>;evidence:Record<string,unknown>;missing:string[];mentions:Mention[];suggestions:Place[];notes:string[];search_source:string}
const guideOpen=ref(false)
function importPlaces(items:Place[],optional:string[]){const merged=mergeImportedOptional(candidateIds.value,optionalIds.value,items.map(p=>p.id),optional);for(const p of items)choose(p);optionalIds.value=merged.filter(id=>candidateIds.value.includes(id));guideOpen.value=false}
const step=ref(0),intent=ref(''),title=ref('我的杭州慢游'),error=ref(''),busy=ref(false),parsing=ref(false),useModel=ref(props.hasModel),agentOpen=ref(false)
const conversationId=ref(''),conversationOpen=ref(false),conversationBusy=ref(false)
const recentConversations=ref<{runId:string;title:string;status:string}[]>([])
const conversationKey='tripcanvas.conversation.active.v1'
const req=ref<Requirements>({city:'杭州',startDate:'',days:2,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[],originPlaceId:null,destinationPlaceId:null,returnBy:null,walkBudgetKm:null})
const manualIds=ref<string[]>([])
const parsed=ref<Parsed|null>(null)
const chosen=ref<Record<string,string>>({})
const candidateIds=computed(()=>candidateSelection(manualIds.value,parsed.value?.mentions??[],chosen.value,[req.value.originPlaceId,req.value.destinationPlaceId]))
const reviewRequired=ref(false),repairSummary=ref('')
const optionalIds=ref<string[]>([]),comparison=ref<Comparison|null>(null),selectedOption=ref('')
watch(candidateIds,ids=>optionalIds.value=optionalIds.value.filter(id=>ids.includes(id)))
interface RepairOption {action:string;activityId?:string;minutes?:number;day?:number}
interface DraftIssue {message:string;severity?:'error'|'warning';code?:string;options?:RepairOption[]}
const remembered=ref<Record<string,unknown>>({})
const draft=ref<{trip:Trip;source:string;issues:DraftIssue[];warnings:string[];routes?:Record<string,RoadRoute>}|null>(null)
const draftErrors=computed(()=>draft.value?.issues.filter(issue=>issue.severity!=='warning')??[])
const draftWarnings=computed(()=>draft.value?.issues.filter(issue=>issue.severity==='warning')??[])
const valid=computed(()=>!!req.value.startDate&&req.value.days>=1&&req.value.days<=3&&req.value.start<req.value.end&&req.value.people>=1&&req.value.people<=10)
const selected=computed(()=>candidateIds.value.map(id=>placeById(id)))
const missing=computed(()=>parsed.value?.missing??[])
const unidentified=computed(()=>(parsed.value?.mentions??[]).filter(m=>!chosen.value[mentionKey(m)]))
const evidence=computed(()=>{
 const e=parsed.value?.evidence??{}
 const labels:[string,boolean][]=[['日期',!!e.startDate],['天数',!!e.days],['人数',!!e.people],['交通',!!e.mode],['节奏',!!e.pace],['兴趣',Array.isArray(e.interests)&&e.interests.length>0],['返程时间',!!e.returnBy],['起终点',!!(e.origin||e.destination)]]
 return labels
})
watch(()=>props.hasModel,value=>useModel.value=value)
onMounted(async()=>{
 if(!props.loggedIn)return
 try{
  const saved=await api('/preferences')
  if(saved&&Object.keys(saved).length){
   if(saved.pace)req.value.pace=saved.pace
   if(Array.isArray(saved.interests)&&saved.interests.length)req.value.interests=saved.interests
   if(typeof saved.walkBudgetKm==='number')req.value.walkBudgetKm=saved.walkBudgetKm
   remembered.value=saved
  }
 }catch{}
})
function minutes(value:string){const [h,m]=value.split(':').map(Number);return h!*60+m!}
function updateTime(event:Event,key:'start'|'end'|'returnBy'){const value=(event.target as HTMLInputElement).value;req.value[key]=value?minutes(value):null as never}
function resetConversation(){conversationId.value='';conversationOpen.value=false;try{sessionStorage.removeItem(conversationKey)}catch{}}
function conversationCreated(trip:Trip){resetConversation();emit('created',trip)}
async function converse(){
 if(!props.loggedIn){emit('login');return}
 if(conversationId.value){conversationOpen.value=true;return}
 if(intent.value.trim().length<2){error.value='先写下你的出行想法，缺少的条件可以在对话中补充。';return}
 conversationBusy.value=true;error.value=''
 try{
  const result=await beginConversation(intent.value.trim(),title.value)
  conversationId.value=result.runId;conversationOpen.value=true
  try{sessionStorage.setItem(conversationKey,JSON.stringify({id:result.runId}))}catch{}
 }catch(e){error.value=(e as Error).message}
 finally{conversationBusy.value=false}
}
async function loadRecent(){if(!props.loggedIn)return;try{const result=await api('/planner/conversations');if(Array.isArray(result))recentConversations.value=result}catch{}}
function resumeConversation(id:string){conversationId.value=id;conversationOpen.value=true;try{sessionStorage.setItem(conversationKey,JSON.stringify({id}))}catch{}}
onMounted(()=>{try{const saved=JSON.parse(sessionStorage.getItem(conversationKey)||'null');if(saved&&typeof saved.id==='string')conversationId.value=saved.id}catch{}void loadRecent()})
watch(()=>props.loggedIn,value=>{if(value)void loadRecent();else{resetConversation();recentConversations.value=[]}})
async function interpret(){
 if(parsing.value)return
 if(!props.loggedIn){emit('login');return}
 const text=intent.value.trim()
 if(text.length<2){error.value='请先写下一句出行需求，例如日期、人数、想去的地方和返程要求';return}
 parsing.value=true;error.value=''
 try{
  const data:Parsed=await api('/planner/requirements','POST',{text})
  parsed.value=data
  chosen.value={}
  req.value={...req.value,originPlaceId:null,destinationPlaceId:null,returnBy:null,...data.requirements}
  const found=[...data.mentions.flatMap(m=>m.candidates),...data.suggestions]
  registerPlaces(found)
  if(data.notes.length)error.value=data.notes.join('；')
 }catch(e){error.value=(e as Error).message}
 finally{parsing.value=false}
}
function resolve(mention:Mention,place:Place){
 const key=mentionKey(mention)
 if(mention.role==='visit'&&!chosen.value[key]&&candidateIds.value.length>=18){error.value='最多选择 18 个游览地点';return}
 chosen.value={...chosen.value,[key]:place.id}
 if(mention.role==='destination')req.value.destinationPlaceId=place.id
 else if(mention.role==='origin')req.value.originPlaceId=place.id
 error.value=''
}
function choose(p:Place){if(candidateIds.value.length>=18&&!candidateIds.value.includes(p.id)){error.value='最多选择 18 个游览地点';return}manualIds.value=[...new Set([...manualIds.value,p.id])]}
function removeCandidate(id:string){manualIds.value=manualIds.value.filter(value=>value!==id);for(const mention of parsed.value?.mentions??[])if(mention.role==='visit'&&chosen.value[mentionKey(mention)]===id)delete chosen.value[mentionKey(mention)]}
function skipMention(mention:Mention){if(parsed.value)parsed.value.mentions=parsed.value.mentions.filter(m=>mentionKey(m)!==mentionKey(mention))}
function draftEntries(day:number){if(!draft.value)return [];return schedule(draft.value.trip,day,draft.value.routes?.[String(day)])}
function next(){
 error.value=''
 if(!valid.value){error.value='请确认具体日期、人数和每日时间范围';return}
 if(unidentified.value.length){error.value='请先确认识别出的地点；不需要的地点可以点“跳过”';return}
 if(parsed.value?.missing.includes('returnBy')&&req.value.returnBy==null){error.value='请确认返程具体时刻，不能省略尚有歧义的返程要求';return}
 const deadline=req.value.returnBy
 if(deadline!==null&&deadline!==undefined&&(!req.value.destinationPlaceId||deadline<req.value.start||deadline>req.value.end-15)){error.value='请确认返程地点和时间，至少预留 15 分钟';return}
 step.value=1
}
async function generate(){
 if(busy.value)return
 if(!props.loggedIn){emit('login');return}
 busy.value=true;error.value='';draft.value=null;comparison.value=null;selectedOption.value=''
 try{
  const walkBudgetKm=typeof req.value.walkBudgetKm==='number'&&req.value.walkBudgetKm>=0.5?req.value.walkBudgetKm:null
  const data=await api('/planner/draft','POST',{title:title.value,requirements:{...req.value,walkBudgetKm},candidateIds:candidateIds.value,useModel:useModel.value})
  registerPlaces(data.trip.placeSnapshots)
  draft.value=data;reviewRequired.value=false;repairSummary.value='';step.value=2
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}

async function comparePlans(){
 if(busy.value)return
 if(!props.loggedIn){emit('login');return}
 busy.value=true;error.value='';draft.value=null;comparison.value=null;selectedOption.value=''
 try{
  const result:Comparison=await api('/planner/alternatives','POST',{title:title.value,requirements:{...req.value,walkBudgetKm:req.value.walkBudgetKm||null},candidateIds:candidateIds.value,optionalIds:optionalIds.value,useModel:useModel.value})
  comparison.value=result
  for(const option of result.options)registerPlaces(option.trip.placeSnapshots)
  if(result.options.length)selectOption(result.options[0]!.id,true)
  else if(result.diagnostic){registerPlaces(result.diagnostic.trip.placeSnapshots);draft.value={...result.diagnostic,source:result.source,warnings:result.warnings};reviewRequired.value=false}
  step.value=2
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
function selectOption(id:string,initial=false){
 if(busy.value&&!initial)return
 const option=comparison.value?.options.find(item=>item.id===id);if(!option)return
 draft.value={...clone(option),source:comparison.value!.source,warnings:comparison.value!.warnings}
 selectedOption.value=id;reviewRequired.value=false;repairSummary.value='';error.value=''
}

function draftWalk(day:number):number|null{
 if(!draft.value?.routes)return null
 return walkingKm(draft.value.routes[String(day)],draft.value.trip.mode)
}
function placeName(activityId?:string){const hit=draft.value?.trip.days.flatMap(day=>day.activities).find(activity=>activity.id===activityId);return hit?placeById(hit.placeId).name:'该活动'}
function optionLabel(option:RepairOption){
 if(option.action==='shorten')return `缩短 ${placeName(option.activityId)} ${option.minutes} 分钟`
 if(option.action==='extend_day')return `当天结束延后 ${option.minutes} 分钟`
 if(option.action==='depart_earlier')return `提前 ${option.minutes} 分钟出发`
 if(option.action==='drop')return `移除 ${placeName(option.activityId)}`
 return '重新生成草案'
}
async function recheck(next:Trip){
 if(!draft.value)return
 busy.value=true;reviewRequired.value=true;error.value=''
 try{
  const result=await api('/planner/preview','POST',{trip:next})
  registerPlaces(result.trip.placeSnapshots)
  draft.value={...draft.value,...result};reviewRequired.value=false
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
async function applyRepair(option:RepairOption){
 if(!draft.value||busy.value)return
 if(option.action==='retry_route'){await recheck(draft.value.trip);return}
 const next=clone(draft.value.trip)
 selectedOption.value=''
 if(option.action==='shorten'&&option.activityId){
  for(const day of next.days){const activity=day.activities.find(item=>item.id===option.activityId);if(activity){activity.duration=Math.max(15,activity.duration-(option.minutes||0));break}}
 } else if(option.action==='extend_day'&&option.day!==undefined){
  const day=next.days[option.day];if(day)day.end=Math.min(1440,day.end+(option.minutes||0))
 } else if(option.action==='depart_earlier'&&option.day!==undefined){
  const day=next.days[option.day];if(day)day.start=Math.max(0,day.start-(option.minutes||0))
 } else if(option.action==='drop'&&option.activityId){
  for(const day of next.days){const index=day.activities.findIndex(item=>item.id===option.activityId&&item.lock==='none');if(index>=0){day.activities.splice(index,1);break}}
 }
 repairSummary.value=optionLabel(option)+'；正在重算，原旅行尚未保存。'
 draft.value={...draft.value,trip:next,routes:undefined}
 await recheck(next)
 if(!reviewRequired.value)repairSummary.value=optionLabel(option)+'；已重算，请核对逐站时间与剩余问题，再确认保存。'
}
async function save(){
 if(!draft.value||draftErrors.value.length||busy.value||reviewRequired.value)return
 busy.value=true;error.value=''
 try{
  const result=await confirmTrip(draft.value.trip,0)
  registerPlaces(result.trip.placeSnapshots)
  emit('created',result.trip)
 }catch(e){
  if(e instanceof ApiError&&e.detail&&typeof e.detail==='object'&&'issues' in e.detail){
   const detail=e.detail as {issues:DraftIssue[];routes?:Record<string,RoadRoute>}
   draft.value={...draft.value,issues:detail.issues,routes:detail.routes}
  }
  error.value=(e as Error).message
 }
 finally{busy.value=false}
}
</script>
<template>
 <div class="trip-builder">
  <span class="eyebrow">YOUR HANGZHOU JOURNEY STARTS HERE</span>
  <h2>{{['这次，想怎么玩杭州？','选定，真正想去的地方。','看看这份杭州草案。'][step]}}</h2>
  <p v-if="Object.keys(remembered).length" class="form-hint">已带入上次偏好：{{ remembered.pace==='relaxed'?'轻松':remembered.pace==='full'?'充实':'适中' }}{{ Array.isArray(remembered.interests)&&remembered.interests.length?' · '+remembered.interests.join(' / '):'' }}{{ remembered.walkBudgetKm?' · 步行 '+remembered.walkBudgetKm+' km':'' }}（可直接修改）</p>
  <div class="builder-steps"><span v-for="(label,i) in ['确认需求','选择地点','预览保存']" :key="label" :class="{active:step===i}">{{i+1}} · {{label}}</span></div>

  <template v-if="step===0">
   <label>出行想法
    <textarea v-model="intent" :disabled="conversationOpen" rows="3" maxlength="1000" placeholder="例如：2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。"/>
   </label>
   <div class="conversation-entry">
    <button class="button primary" @click="converse" :disabled="conversationBusy||!hasModel||conversationOpen"><LoaderCircle v-if="conversationBusy" class="spin" :size="14"/><Sparkles v-else :size="14"/>{{conversationBusy?'正在开启对话…':conversationId?'继续刚才的 AI 任务':'用 AI 对话规划'}}</button>
    <button v-if="conversationId" class="text-button" @click="resetConversation" :disabled="conversationBusy">重新描述新任务</button>
   </div>
   <details v-if="recentConversations.length&&!conversationOpen" class="recent-conversations"><summary>继续最近的规划任务</summary><button v-for="item in recentConversations" :key="item.runId" class="button quiet full" @click="resumeConversation(item.runId)">{{item.title}} · {{item.status==='review'?'待确认':item.status==='needs_input'?'待回答':item.status==='running'?'处理中':'可继续'}}</button></details>
   <p class="form-hint">可以先说想法，再补日期、人数和偏好。AI 会反问并搜索真实地点，你确认后再规划；支持杭州 1—3 日步行或自驾接驳。</p>
   <AgentTrace v-if="conversationOpen&&conversationId" :key="conversationId" :initial-run-id="conversationId" :title="title" :requirements="req" :candidate-ids="[]" :logged-in="loggedIn" @created="conversationCreated"/>
   <p v-if="conversationOpen&&error" class="form-error" role="alert">{{error}}</p>
   <template v-if="!conversationOpen">
   <button class="button quiet small" @click="interpret" :disabled="parsing">
    <LoaderCircle v-if="parsing" class="spin" :size="14"/><Sparkles v-else :size="14"/>{{parsing?'正在识别并搜索地点…':'识别需求（规则解析）'}}
   </button>
   <p class="form-hint">也可使用下方规则解析和手动表单。规则解析只提取文字里的条件，不会猜测日期或地点；识别出的地点需要你按校区、地址确认。</p>

   <template v-if="parsed">
    <div class="evidence"><span v-for="[label,ok] in evidence" :key="label" :class="{missing:!ok}">{{ok?'已识别':'未提及'}} · {{label}}</span></div>
    <p v-if="missing.length" class="form-error"><AlertTriangle :size="13"/>还需要确认：{{missing.join('、')}}。下面可直接手动填写。</p>
    <div v-for="mention in parsed.mentions" :key="mentionKey(mention)" class="mention-card">
     <strong>{{mention.role==='destination'?'返程地点':mention.role==='origin'?'出发点':'想去'}}：{{mention.text}}</strong>
     <p v-if="mention.error" class="form-error">{{mention.error}}</p>
     <p v-else-if="!mention.candidates.length" class="form-hint">没有找到匹配的真实地点，请换用更具体的名称搜索。</p>
     <div v-else class="mention-options">
      <button v-for="place in mention.candidates" :key="place.id" :class="{chosen:chosen[mentionKey(mention)]===place.id}" @click="resolve(mention,place)" :aria-label="`确认${mention.text}为${place.name}`">
       {{place.name}}<small>{{place.address}}</small>
      </button>
     </div>
     <button v-if="mention.role!=='destination'" class="button quiet small" @click="skipMention(mention)">跳过这个地点</button>
    </div>
    <p v-if="unidentified.length" class="form-hint">未确认地点不会进入草案：{{unidentified.map(m=>m.text).join('、')}}。</p>
   </template>

   <label>旅行名称<input v-model="title" maxlength="80"/></label>
   <div class="form-row">
    <label>城市<input value="杭州" readonly/></label>
    <label>出发日期<input type="date" v-model="req.startDate" required/></label>
   </div>
   <div class="form-row">
    <label>旅行天数<select v-model.number="req.days"><option v-for="n in 3" :key="n" :value="n">{{n}} 天</option></select></label>
    <label>同行人数<input type="number" v-model.number="req.people" min="1" max="10"/></label>
    <label>交通方式<select v-model="req.mode"><option value="walk">步行</option><option value="drive">驾车</option></select></label>
   </div>
   <div class="form-row">
    <label>每天开始<input type="time" :value="time(req.start)" @change="updateTime($event,'start')"/></label>
    <label>每天结束<input type="time" :value="time(req.end)" @change="updateTime($event,'end')"/></label>
    <label>节奏<select v-model="req.pace"><option value="relaxed">轻松</option><option value="balanced">适中</option><option value="full">充实</option></select></label>
   </div>
   <label>每日步行预算（公里，可选）<input type="number" min="0.5" max="30" step="0.5" v-model.number="req.walkBudgetKm" placeholder="留空表示不限制"/></label>
   <p class="form-hint">预算按高德分段路线中的步行接驳累计；无法拆分步行距离时，不能确认满足预算。</p>
   <div class="field-label">兴趣偏好
    <div class="interest-options"><label v-for="interest in ['咖啡','拍照','博物馆','美术','自然','美食']" :key="interest"><input type="checkbox" :value="interest" v-model="req.interests"/>{{interest}}</label></div>
   </div>
   <div class="form-row">
    <label>首日起点（可选）<select v-model="req.originPlaceId"><option :value="null">不指定</option><option v-for="p in places" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
    <label>末日终点（可选）<select v-model="req.destinationPlaceId"><option :value="null">不指定</option><option v-for="p in places" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
   </div>
   <label v-if="req.destinationPlaceId||req.returnBy!=null||parsed?.missing.includes('returnBy')">末日最晚到达（可选）<input type="time" :value="req.returnBy==null?'':time(req.returnBy)" @change="updateTime($event,'returnBy')"/></label>
   <p v-if="!req.destinationPlaceId&&req.returnBy!=null" class="form-hint">已填写最晚到达时间，请选择终点；不需要返程限制时请清空时间。</p>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <button class="button primary full" @click="next">确认需求，选择地点<ArrowRight :size="16"/></button>
   </template>
  </template>

  <template v-else-if="step===1">
   <div class="requirements-summary">{{req.startDate}} · {{req.days}} 天 · {{req.people}} 人 · {{req.mode==='walk'?'步行':'驾车'}} · {{time(req.start)}}—{{time(req.end)}}<span>{{req.interests.join(' / ')||'无特别偏好'}}{{req.walkBudgetKm?' · 步行预算 '+req.walkBudgetKm+' km':''}}{{req.returnBy!==null?' · 最晚 '+time(req.returnBy!)+' 到达':''}}</span></div>
   <div class="candidate-list">
    <strong>游览候选 · {{candidateIds.length}} 个</strong>
    <p v-for="p in selected" :key="p.id"><MapPin :size="13"/><span>{{p.name}}</span><label class="optional-choice"><input type="checkbox" v-model="optionalIds" :value="p.id"/>可选</label><button @click="removeCandidate(p.id)" :aria-label="`移除候选${p.name}`"><Trash2 :size="13"/></button></p>
    <p v-if="!candidateIds.length" class="form-hint">还没有候选地点：先确认上方识别出的地点，或用下面的搜索添加。</p>
   </div>
   <p class="form-hint">多方案默认将候选视为必去；勾选“可选”才允许某套方案舍弃它。会尝试跨日分配、顺序与合理停留范围，保留固定返程和时间条件。</p>
   <p v-if="req.originPlaceId||req.destinationPlaceId" class="form-hint">起终点会独立添加，请从游览候选中移除同一地点。</p>
   <button class="button quiet full" @click="guideOpen=!guideOpen">{{guideOpen?'收起攻略导入':'从收藏的攻略批量导入地点'}}</button>
   <GuideImporter v-if="guideOpen" :logged-in="loggedIn" @login="emit('login')" @choose="importPlaces"/>
   <PlacePicker :selected="candidateIds" @choose="choose"/>
   <template v-if="parsed&&parsed.suggestions.length">
    <p class="form-hint">按兴趣补充的候选（可跳过）：</p>
    <div class="suggestion-list"><button v-for="p in parsed.suggestions" :key="p.id" :disabled="candidateIds.includes(p.id)" @click="choose(p)">{{candidateIds.includes(p.id)?'已选':'+ '}}{{p.name}}</button></div>
   </template>
   <div class="agent-entry"><button type="button" class="button quiet small" @click="agentOpen=!agentOpen" :disabled="!agentOpen&&(!loggedIn||!hasModel||candidateIds.length<req.days||busy)">{{agentOpen?'收起 Agent 实验':'用 Agent 规划（实验）'}}</button><span class="form-hint">模型多步调用路线/校验工具；方案仍需确认才保存。</span></div>
   <AgentTrace v-if="agentOpen" :title="title" :requirements="req" :candidate-ids="candidateIds" :optional-ids="optionalIds" :logged-in="loggedIn" @created="t=>emit('created',t)"/>
   <label class="model-choice"><input type="checkbox" v-model="useModel" :disabled="!hasModel"/>{{hasModel?'使用模型分配日期和顺序':'当前为规则规划，将使用地理邻近草案'}}</label>
   <p class="form-hint">{{useModel?'将把确认的需求和候选地点发给模型，随后程序核验地点和道路用时。':'规则草案使用确认的地点和道路估时，不代表 AI 生成，也不保证全局最优。'}}</p>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <div class="modal-actions">
    <button class="button quiet" @click="step=0" :disabled="busy"><ArrowLeft :size="15"/>修改需求</button>
    <button class="button primary" @click="generate" :disabled="busy||candidateIds.length<req.days"><LoaderCircle v-if="busy" class="spin" :size="15"/>{{busy?'生成并核验道路…':loggedIn?'生成草案':'先登录再生成'}}</button>
   </div>
   <button class="button primary full compare-start" @click="comparePlans" :disabled="busy||candidateIds.length<req.days"><LoaderCircle v-if="busy" class="spin" :size="15"/>{{busy?'正在搜索并核验可行方案…':'生成多方案 · 主动协调'}}</button>
   <p class="form-hint">多方案需要查询真实道路，复杂行程可能需要一两分钟；搜索有上限，不保证全局最优。</p>
  </template>

  <PlanComparison v-if="step===2&&comparison" :comparison="comparison" :selected="selectedOption" :busy="busy" @choose="selectOption"/>
  <p v-if="step===2&&comparison&&!draft" class="form-error">当前没有可核验的草案，请减少候选、调整交通条件或稍后重试。<button class="button quiet" @click="step=1">返回调整</button></p>
  <template v-if="step===2&&draft">
   <p v-if="repairSummary" class="form-hint" role="status">{{repairSummary}}</p>
   <p v-if="reviewRequired" class="form-error">修改后的道路和时间尚未核验，不能确认保存。<button class="button quiet small" @click="recheck(draft.trip)" :disabled="busy">重新核验当前草案</button></p>
   <p class="draft-source">{{draft.source.includes('search')?'主动协调草案 · 道路与硬约束已核验':draft.source==='model'?'模型生成 · 程序已校验道路':'规则草案 · 已校验道路用时'}}</p>
   <section v-for="(day,i) in draft.trip.days" :key="day.date" class="draft-day">
    <h3>Day {{i+1}} · {{day.date}}<small v-if="draftWalk(i)!==null">步行约 {{draftWalk(i)!.toFixed(1)}} km</small></h3>
    <p v-for="entry in draftEntries(i)" :key="entry.activity.id"><span>{{entry.place.name}}</span><small>{{!reviewRequired?time(entry.start)+'—'+time(entry.end)+' · ':''}}{{entry.activity.duration}} 分钟{{entry.activity.lock==='fixed'?' · 固定到达 '+time(entry.activity.fixedStart!):''}}</small></p>
   </section>
   <div v-for="(issue,index) in draftErrors" :key="'error'+index" class="issue-repair">
    <p class="form-error"><AlertTriangle :size="13"/>{{issue.message}}</p>
    <div v-if="issue.options&&issue.options.length" class="repair-options">
     <button v-for="(option,position) in issue.options" :key="position" @click="applyRepair(option)">{{optionLabel(option)}}</button>
    </div>
   </div>
   <p v-for="(issue,index) in draftWarnings" :key="'warning'+index" class="form-hint"><AlertTriangle :size="12"/>{{issue.message}}</p>
   <p v-for="warning in draft.warnings" :key="warning" class="form-hint">{{warning}}</p>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <div class="modal-actions">
    <button class="button quiet" @click="step=1" :disabled="busy">返回调整</button>
    <button class="button primary" @click="save" :disabled="busy||reviewRequired||draftErrors.length>0"><LoaderCircle v-if="busy" class="spin" :size="15"/><Check v-else :size="16"/>{{busy?'正在核验…':'确认并保存旅行'}}</button>
   </div>
   <p v-if="draftErrors.length" class="form-hint">存在冲突时不会保存，请选择修复或调整条件后重新生成。</p>
  </template>
 </div>
</template>

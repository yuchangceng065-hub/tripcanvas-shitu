<script setup lang="ts">
import {ref,computed,watch} from 'vue'
import {Check,ArrowRight,ArrowLeft,LoaderCircle,Trash2,MapPin,AlertTriangle,Sparkles} from '@lucide/vue'
import PlacePicker from './PlacePicker.vue'
import {clone,places,placeById,registerPlaces,time,type Requirements,type Trip,type Place} from '../domain'
import {api,confirmTrip} from '../api'
const props=defineProps<{loggedIn:boolean;hasModel:boolean}>()
const emit=defineEmits<{created:[trip:Trip];login:[]}>()
type Role='visit'|'destination'|'origin'
interface Mention {text:string;role:Role;candidates:Place[];error?:string}
interface Parsed {requirements:Partial<Requirements>;evidence:Record<string,unknown>;missing:string[];mentions:Mention[];suggestions:Place[];notes:string[];search_source:string}
const step=ref(0),intent=ref(''),title=ref('我的杭州慢游'),error=ref(''),busy=ref(false),parsing=ref(false),useModel=ref(props.hasModel)
const req=ref<Requirements>({city:'杭州',startDate:'',days:2,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[],originPlaceId:null,destinationPlaceId:null,returnBy:null})
const candidateIds=ref<string[]>([])
const parsed=ref<Parsed|null>(null)
const chosen=ref<Record<string,string>>({})
interface RepairOption {action:string;activityId?:string;minutes?:number;day?:number}
interface DraftIssue {message:string;options?:RepairOption[]}
const draft=ref<{trip:Trip;source:string;issues:DraftIssue[];warnings:string[]}|null>(null)
const valid=computed(()=>!!req.value.startDate&&req.value.days>=1&&req.value.days<=3&&req.value.start<req.value.end&&req.value.people>=1&&req.value.people<=10)
const selected=computed(()=>candidateIds.value.map(id=>placeById(id)))
const missing=computed(()=>parsed.value?.missing??[])
const unidentified=computed(()=>(parsed.value?.mentions??[]).filter(m=>!chosen.value[m.text]))
const evidence=computed(()=>{
 const e=parsed.value?.evidence??{}
 const labels:[string,boolean][]=[['日期',!!e.startDate],['天数',!!e.days],['人数',!!e.people],['交通',!!e.mode],['节奏',!!e.pace],['兴趣',Array.isArray(e.interests)&&e.interests.length>0],['返程时间',!!e.returnBy],['起终点',!!(e.origin||e.destination)]]
 return labels
})
watch(()=>props.hasModel,value=>useModel.value=value)
function minutes(value:string){const [h,m]=value.split(':').map(Number);return h!*60+m!}
function updateTime(event:Event,key:'start'|'end'|'returnBy'){const value=(event.target as HTMLInputElement).value;req.value[key]=value?minutes(value):null as never}
async function interpret(){
 if(parsing.value)return
 if(!props.loggedIn){emit('login');return}
 const text=intent.value.trim()
 if(text.length<2){error.value='请先写下一句出行需求，例如日期、人数、想去的地方和返程要求';return}
 parsing.value=true;error.value=''
 try{
  const data:Parsed=await api('/planner/requirements','POST',{text})
  parsed.value=data
  req.value={...req.value,...data.requirements}
  const found=[...data.mentions.flatMap(m=>m.candidates),...data.suggestions]
  registerPlaces(found)
  if(data.notes.length)error.value=data.notes.join('；')
 }catch(e){error.value=(e as Error).message}
 finally{parsing.value=false}
}
function resolve(mention:Mention,place:Place){
 chosen.value={...chosen.value,[mention.text]:place.id}
 if(mention.role==='destination'){req.value.destinationPlaceId=place.id;candidateIds.value=candidateIds.value.filter(id=>id!==place.id)}
 else if(mention.role==='origin'){req.value.originPlaceId=place.id;candidateIds.value=candidateIds.value.filter(id=>id!==place.id)}
 else if(!candidateIds.value.includes(place.id)&&candidateIds.value.length<18)candidateIds.value.push(place.id)
 error.value=''
}
function choose(p:Place){if(candidateIds.value.length>=18){error.value='最多选择 18 个游览地点';return}if(!candidateIds.value.includes(p.id))candidateIds.value.push(p.id)}
function next(){
 error.value=''
 if(!valid.value){error.value='请确认具体日期、人数和每日时间范围';return}
 const deadline=req.value.returnBy
 if(deadline!==null&&deadline!==undefined&&(!req.value.destinationPlaceId||deadline<req.value.start||deadline>req.value.end-15)){error.value='请确认返程地点和时间，至少预留 15 分钟';return}
 step.value=1
}
async function generate(){
 if(busy.value)return
 if(!props.loggedIn){emit('login');return}
 busy.value=true;error.value='';draft.value=null
 try{
  const data=await api('/planner/draft','POST',{title:title.value,requirements:req.value,candidateIds:candidateIds.value,useModel:useModel.value})
  registerPlaces(data.trip.placeSnapshots)
  draft.value=data;step.value=2
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}

function placeName(activityId?:string){const hit=draft.value?.trip.days.flatMap(day=>day.activities).find(activity=>activity.id===activityId);return hit?placeById(hit.placeId).name:'该活动'}
function optionLabel(option:RepairOption){
 if(option.action==='shorten')return `缩短 ${placeName(option.activityId)} ${option.minutes} 分钟`
 if(option.action==='extend_day')return `当天结束延后 ${option.minutes} 分钟`
 if(option.action==='depart_earlier')return `提前 ${option.minutes} 分钟出发`
 if(option.action==='drop')return `移除 ${placeName(option.activityId)}`
 return '重新生成草案'
}
function applyRepair(option:RepairOption){
 if(!draft.value||busy.value)return
 if(option.action==='retry_route'){void generate();return}
 const next=clone(draft.value.trip)
 if(option.action==='shorten'&&option.activityId){
  for(const day of next.days){const activity=day.activities.find(item=>item.id===option.activityId);if(activity){activity.duration=Math.max(15,activity.duration-(option.minutes||0));break}}
 } else if(option.action==='extend_day'&&option.day!==undefined){
  const day=next.days[option.day];if(day)day.end=Math.min(1440,day.end+(option.minutes||0))
 } else if(option.action==='depart_earlier'&&option.day!==undefined){
  const day=next.days[option.day];if(day)day.start=Math.max(0,day.start-(option.minutes||0))
 } else if(option.action==='drop'&&option.activityId){
  for(const day of next.days){const index=day.activities.findIndex(item=>item.id===option.activityId&&item.lock==='none');if(index>=0){day.activities.splice(index,1);break}}
 }
 draft.value={...draft.value,trip:next,issues:[]}
 void save()
}
async function save(){
 if(!draft.value||draft.value.issues.length||busy.value)return
 busy.value=true;error.value=''
 try{
  const result=await confirmTrip(draft.value.trip,0)
  registerPlaces(result.trip.placeSnapshots)
  emit('created',result.trip)
 }catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
</script>
<template>
 <div class="trip-builder">
  <span class="eyebrow">YOUR HANGZHOU JOURNEY STARTS HERE</span>
  <h2>{{['这次，想怎么玩杭州？','选定，真正想去的地方。','看看这份杭州草案。'][step]}}</h2>
  <div class="builder-steps"><span v-for="(label,i) in ['确认需求','选择地点','预览保存']" :key="label" :class="{active:step===i}">{{i+1}} · {{label}}</span></div>

  <template v-if="step===0">
   <label>出行想法
    <textarea v-model="intent" rows="3" maxlength="1000" placeholder="例如：2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。"/>
   </label>
   <button class="button quiet small" @click="interpret" :disabled="parsing">
    <LoaderCircle v-if="parsing" class="spin" :size="14"/><Sparkles v-else :size="14"/>{{parsing?'正在识别并搜索地点…':'识别需求（规则解析）'}}
   </button>
   <p class="form-hint">规则解析只提取文字里的条件，不会猜测日期或地点；识别出的地点需要你按校区、地址确认。</p>

   <template v-if="parsed">
    <div class="evidence"><span v-for="[label,ok] in evidence" :key="label" :class="{missing:!ok}">{{ok?'已识别':'未提及'}} · {{label}}</span></div>
    <p v-if="missing.length" class="form-error"><AlertTriangle :size="13"/>还需要确认：{{missing.join('、')}}。下面可直接手动填写。</p>
    <div v-for="mention in parsed.mentions" :key="mention.text" class="mention-card">
     <strong>{{mention.role==='destination'?'返程地点':mention.role==='origin'?'出发点':'想去'}}：{{mention.text}}</strong>
     <p v-if="mention.error" class="form-error">{{mention.error}}</p>
     <p v-else-if="!mention.candidates.length" class="form-hint">没有找到匹配的真实地点，请换用更具体的名称搜索。</p>
     <div v-else class="mention-options">
      <button v-for="place in mention.candidates" :key="place.id" :class="{chosen:chosen[mention.text]===place.id}" @click="resolve(mention,place)" :aria-label="`确认${mention.text}为${place.name}`">
       {{place.name}}<small>{{place.address}}</small>
      </button>
     </div>
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
   <div class="field-label">兴趣偏好
    <div class="interest-options"><label v-for="interest in ['咖啡','拍照','博物馆','美术','自然','美食']" :key="interest"><input type="checkbox" :value="interest" v-model="req.interests"/>{{interest}}</label></div>
   </div>
   <div class="form-row">
    <label>首日起点（可选）<select v-model="req.originPlaceId"><option :value="null">不指定</option><option v-for="p in places" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
    <label>末日终点（可选）<select v-model="req.destinationPlaceId"><option :value="null">不指定</option><option v-for="p in places" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
   </div>
   <label v-if="req.destinationPlaceId">末日最晚到达（可选）<input type="time" :value="req.returnBy===null?'':time(req.returnBy!)" @change="updateTime($event,'returnBy')"/></label>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <button class="button primary full" @click="next">确认需求，选择地点<ArrowRight :size="16"/></button>
  </template>

  <template v-else-if="step===1">
   <div class="requirements-summary">{{req.startDate}} · {{req.days}} 天 · {{req.people}} 人 · {{req.mode==='walk'?'步行':'驾车'}} · {{time(req.start)}}—{{time(req.end)}}<span>{{req.interests.join(' / ')||'无特别偏好'}}{{req.returnBy!==null?' · 最晚 '+time(req.returnBy!)+' 到达':''}}</span></div>
   <div class="candidate-list">
    <strong>游览候选 · {{candidateIds.length}} 个</strong>
    <p v-for="p in selected" :key="p.id"><MapPin :size="13"/><span>{{p.name}}</span><button @click="candidateIds=candidateIds.filter(id=>id!==p.id)" :aria-label="`移除候选${p.name}`"><Trash2 :size="13"/></button></p>
    <p v-if="!candidateIds.length" class="form-hint">还没有候选地点：先确认上方识别出的地点，或用下面的搜索添加。</p>
   </div>
   <p v-if="req.originPlaceId||req.destinationPlaceId" class="form-hint">起终点会独立添加，请从游览候选中移除同一地点。</p>
   <PlacePicker :selected="candidateIds" @choose="choose"/>
   <template v-if="parsed&&parsed.suggestions.length">
    <p class="form-hint">按兴趣补充的候选（可跳过）：</p>
    <div class="suggestion-list"><button v-for="p in parsed.suggestions" :key="p.id" :disabled="candidateIds.includes(p.id)" @click="choose(p)">{{candidateIds.includes(p.id)?'已选':'+ '}}{{p.name}}</button></div>
   </template>
   <label class="model-choice"><input type="checkbox" v-model="useModel" :disabled="!hasModel"/>{{hasModel?'使用模型分配日期和顺序':'当前为规则规划，将使用地理邻近草案'}}</label>
   <p class="form-hint">{{useModel?'将把确认的需求和候选地点发给模型，随后程序核验地点和道路用时。':'规则草案使用确认的地点和道路估时，不代表 AI 生成，也不保证全局最优。'}}</p>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <div class="modal-actions">
    <button class="button quiet" @click="step=0" :disabled="busy"><ArrowLeft :size="15"/>修改需求</button>
    <button class="button primary" @click="generate" :disabled="busy||candidateIds.length<req.days"><LoaderCircle v-if="busy" class="spin" :size="15"/>{{busy?'生成并核验道路…':loggedIn?'生成草案':'先登录再生成'}}</button>
   </div>
  </template>

  <template v-else-if="draft">
   <p class="draft-source">{{draft.source==='model'?'模型生成 · 程序已校验道路':'规则草案 · 已校验道路用时'}}</p>
   <section v-for="(day,i) in draft.trip.days" :key="day.date" class="draft-day">
    <h3>Day {{i+1}} · {{day.date}}</h3>
    <p v-for="a in day.activities" :key="a.id"><span>{{placeById(a.placeId).name}}</span><small>{{a.duration}} 分钟{{a.lock==='fixed'?' · 固定到达 '+time(a.fixedStart!):''}}</small></p>
   </section>
   <div v-for="(issue,index) in draft.issues" :key="index" class="issue-repair">
    <p class="form-error"><AlertTriangle :size="13"/>{{issue.message}}</p>
    <div v-if="issue.options&&issue.options.length" class="repair-options">
     <button v-for="(option,position) in issue.options" :key="position" @click="applyRepair(option)">{{optionLabel(option)}}</button>
    </div>
   </div>
   <p v-for="warning in draft.warnings" :key="warning" class="form-hint">{{warning}}</p>
   <p v-if="error" class="form-error" role="alert">{{error}}</p>
   <div class="modal-actions">
    <button class="button quiet" @click="step=1" :disabled="busy">返回调整</button>
    <button class="button primary" @click="save" :disabled="busy||draft.issues.length>0"><LoaderCircle v-if="busy" class="spin" :size="15"/><Check v-else :size="16"/>{{busy?'正在复核并保存…':'确认并保存旅行'}}</button>
   </div>
   <p v-if="draft.issues.length" class="form-hint">存在冲突时不会保存，请调整每日时间、候选地点或交通方式后重新生成。</p>
  </template>
 </div>
</template>

<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue'
import {api} from '../api'
import {clone,placeById,registerPlaces,schedule,time,type Trip} from '../domain'
import type {RoadRoute,RouteState} from '../routes'
import type {PlanIssue,PlanMetrics} from '../planningOptions'
import TripMap from './TripMap.vue'
import AgentReplay from './AgentReplay.vue'
const props=defineProps<{trip:Trip;loggedIn:boolean}>(),emit=defineEmits<{open:[trip:Trip];login:[]}>()
interface Evaluation {trip:Trip;routes:Record<string,RoadRoute>;issues:PlanIssue[];feasible:boolean;metrics:PlanMetrics;changes?:Change[]}
interface Change {type:string;day?:number;placeId?:string;before?:unknown;after?:unknown}
interface Repair extends Evaluation {id:string;label:string;reason:string}
interface Result {baseline:Evaluation;repairs:Repair[];runId:string;note:string;search:{evaluated:number;maxEvaluations:number;roadQueries:number}}
interface Branch {id:string;label:string;sourceRevision:number;created:number;trip:Trip}
const day=ref(0),departure=ref(props.trip.days[0]!.start),budget=ref(props.trip.requirements?.walkBudgetKm??null),keepDeparture=ref(true),keepDurations=ref(false),keepPlaces=ref(false)
const result=ref<Result|null>(null),selected=ref(''),error=ref(''),busy=ref(false),saving=ref(false),label=ref('我的新安排'),branches=ref<Branch[]>([]),comparing=ref<Evaluation|null>(null),compareLabel=ref(''),comparisonBusy=ref(false),showReplay=ref(false),verified=ref('')
let timer:ReturnType<typeof setTimeout>|undefined,controller:AbortController|undefined,serial=0,saveKey='',saveIdentity=''
const signature=computed(()=>JSON.stringify([props.trip.id,props.trip.revision,day.value,departure.value,budget.value,keepDeparture.value,keepDurations.value,keepPlaces.value]))
const isVerified=computed(()=>verified.value===signature.value)
const trial=computed(()=>{const t=clone(props.trip);t.days[day.value]!.start=departure.value;if(t.requirements)t.requirements.walkBudgetKm=budget.value;return t})
const preview=computed(()=>isVerified.value?(result.value?.repairs.find(r=>r.id===selected.value)??result.value?.baseline):null)
const shown=computed(()=>preview.value?.trip??trial.value)
const road=computed(()=>preview.value?.routes[String(day.value)]??result.value?.baseline.routes[String(day.value)])
const entries=computed(()=>schedule(shown.value,day.value,road.value))
const mapState=computed<RouteState>(()=>road.value?{status:'ready',route:road.value}:{status:'empty'})
const pin=ref('')
const conflicts=computed(()=>preview.value?.issues.filter(i=>i.severity==='error')??[])
function name(id:string){return placeById(id)?.name??id}
function explain(c:Change){if(c.type==='departure')return `Day ${Number(c.day)+1} 出发 ${time(Number(c.before))} → ${time(Number(c.after))}`;if(c.type==='duration')return `${name(c.placeId!)} 停留 ${c.before} → ${c.after} 分钟`;if(c.type==='removed')return `舍弃可选地点 ${name(c.placeId!)}`;if(c.type==='order')return `Day ${Number(c.day)+1} 路线：${(c.after as string[]).map(name).join(' → ')}`;return `步行上限 ${c.before??'不限'} → ${c.after??'不限'} km`}
function changeDay(){departure.value=props.trip.days[day.value]!.start;selected.value=''}
async function loadBranches(){if(!props.loggedIn)return;try{branches.value=await api('/branches?sourceTripId='+encodeURIComponent(props.trip.id))}catch(e){error.value=(e as Error).message}}
async function verify(){if(!props.loggedIn){emit('login');return}clearTimeout(timer);controller?.abort();controller=new AbortController();const ticket=++serial,current=signature.value;busy.value=true;error.value='';selected.value=''
 try{const data:Result=await api('/planner/simulate','POST',{trip:props.trip,day:day.value,start:departure.value,walkBudgetKm:budget.value,keepDeparture:keepDeparture.value,keepDurations:keepDurations.value,keepPlaces:keepPlaces.value},controller.signal);if(ticket!==serial||current!==signature.value)return;result.value=data;verified.value=current;registerPlaces(data.baseline.trip.placeSnapshots);data.repairs.forEach(r=>registerPlaces(r.trip.placeSnapshots))}catch(e){if(ticket===serial&&!controller?.signal.aborted)error.value=(e as Error).message}finally{if(ticket===serial)busy.value=false}}
watch(signature,()=>{verified.value='';selected.value='';controller?.abort();serial++;busy.value=false;clearTimeout(timer);timer=setTimeout(()=>void verify(),900)})
watch(()=>props.trip.id,()=>{result.value=null;comparing.value=null;branches.value=[];day.value=0;departure.value=props.trip.days[0]!.start;budget.value=props.trip.requirements?.walkBudgetKm??null;void loadBranches()})
onMounted(()=>{void loadBranches();void verify()})
onUnmounted(()=>{clearTimeout(timer);controller?.abort();serial++})
async function save(){if(!preview.value?.feasible||!isVerified.value||saving.value)return;saving.value=true;error.value=''
 const identity=JSON.stringify([preview.value.trip,label.value]);if(identity!==saveIdentity){saveIdentity=identity;saveKey=crypto.randomUUID()}
 try{await api('/branches','POST',{sourceTripId:props.trip.id,sourceRevision:props.trip.revision,trip:preview.value.trip,label:label.value.trim(),requestId:saveKey});await loadBranches();error.value='';label.value='我的新安排'}catch(e){error.value=(e as Error).message}finally{saving.value=false}}
async function compare(branch:Branch){comparisonBusy.value=true;error.value='';try{const checked=await api('/branches/'+branch.id+'/preview','POST');registerPlaces(checked.trip.placeSnapshots);comparing.value=checked;compareLabel.value=branch.label}catch(e){error.value=(e as Error).message}finally{comparisonBusy.value=false}}
async function open(branch:Branch){saving.value=true;error.value='';try{const data=await api('/branches/'+branch.id+'/open','POST');registerPlaces(data.trip.placeSnapshots);emit('open',data.trip)}catch(e){error.value=(e as Error).message}finally{saving.value=false}}
async function remove(branch:Branch){saving.value=true;error.value='';try{await api('/branches/'+branch.id,'DELETE');await loadBranches();comparing.value=null}catch(e){error.value=(e as Error).message}finally{saving.value=false}}
</script>
<template>
 <section class="planning-lab" aria-label="交互式行程推演台">
  <span class="eyebrow">A LITTLE CHANGE. A DIFFERENT JOURNEY.</span><h2>给旅程，试一种可能。</h2><p class="modal-description">调整条件，看看安排如何变化。原计划保留，可行结果可保存为独立分支。</p>
  <div class="lab-grid"><aside class="lab-controls">
   <label>推演哪一天<select v-model.number="day" @change="changeDay"><option v-for="(d,i) in trip.days" :key="d.date" :value="i">Day {{i+1}} · {{d.date}}</option></select></label>
   <label>出发时间 <strong>{{time(departure)}}</strong><input aria-label="推演出发时间" v-model.number="departure" type="range" min="360" :max="Math.min(1260,trip.days[day]!.end-15)" step="15"/></label>
   <label>每日步行上限<select v-model="budget" :disabled="!trip.requirements"><option :value="null">不限制</option><option v-for="km in [1,2,3,5,8,12,20,30]" :value="km" :key="km">{{km}} km</option></select></label>
   <fieldset class="lab-pins"><legend>修复时保留哪些条件？</legend><label><input type="checkbox" v-model="keepDeparture"/>不提前我选定的出发时间</label><label><input type="checkbox" v-model="keepDurations"/>不缩短游览停留</label><label><input type="checkbox" v-model="keepPlaces"/>不舍弃任何地点</label></fieldset>
   <p class="form-hint">固定预约、必去地点、返程与每日结束时间始终保留。步行上限作用于全程各天。</p>
   <button class="button primary full" @click="verify" :disabled="busy">{{busy?'查询道路并寻找修复…':'重新核验与寻找修复'}}</button>
   <p class="lab-status" role="status">{{busy?'正在核验；可继续调整条件':isVerified?'当前条件已完成道路核验':'条件已改变，下方时刻为本地预演，等待道路核验'}}</p>
  </aside><div class="lab-map"><TripMap :entries="entries" :selected="pin" :state="mapState" :mode="shown.mode" @select="pin=$event" @retry="verify"/><div class="lab-map-caption">{{selected?'修复方案':'调整后的安排'}} · {{isVerified?'道路已核验':'尚未核验'}} · {{time(shown.days[day]!.start)}} 出发</div></div></div>
  <p v-if="error" class="form-error" role="alert">{{error}}</p>
  <section class="lab-timeline"><h3>{{preview?.feasible?'当前安排通过硬约束检查':isVerified?'看看冲突发生在哪里':'时间轴预演'}} <small>Day {{day+1}}</small></h3><ol><li v-for="entry in entries" :key="entry.activity.id" :class="{conflict:conflicts.some(i=>(i as any).activityId===entry.activity.id)}"><strong>{{entry.place.name}}</strong><span>{{time(entry.start)}}—{{time(entry.end)}}</span><small>停留 {{entry.activity.duration}} 分钟{{entry.activity.lock==='fixed'?' · 固定安排':entry.activity.lock==='keep'?' · 必须保留':''}}</small></li></ol></section>
  <template v-if="isVerified&&result">
   <div class="lab-metrics" v-if="preview"><span>步行 <strong>{{preview.metrics.walkingKm?.toFixed(1)??'未知'}} km</strong></span><span>交通 <strong>{{preview.metrics.travelMinutes}} 分钟</strong></span><span>停留 <strong>{{preview.metrics.visitMinutes}} 分钟</strong></span><span>空余 <strong>{{preview.metrics.freeMinutes}} 分钟</strong></span></div>
   <div class="conflict-explanation" v-if="conflicts.length"><h3>这些条件暂时无法同时满足</h3><p v-for="(issue,i) in conflicts" :key="i">{{issue.message}}</p><p class="form-hint">下面逐一核验修复候选；不能自动解除固定与返程条件。未找到可行结果时可尝试放开上方某项保护。</p></div>
   <p v-for="issue in preview?.issues.filter(i=>i.severity==='warning')" :key="issue.message" class="form-hint">{{issue.message}}</p>
   <div class="repair-grid" v-if="result.repairs.length"><article v-for="repair in result.repairs" :key="repair.id" :class="{chosen:selected===repair.id}"><span class="repair-badge">{{repair.feasible?'核验可行':'仍有冲突'}}</span><h3>{{repair.label}}</h3><p>{{repair.reason}}</p><ul><li v-for="(change,i) in repair.changes" :key="i">{{explain(change)}}</li></ul><p>步行 {{repair.metrics.walkingKm?.toFixed(1)??'未知'}} km · 交通 {{repair.metrics.travelMinutes}} 分钟 · 空余 {{repair.metrics.freeMinutes}} 分钟</p><p class="form-error" v-for="issue in repair.issues.filter(i=>i.severity==='error')" :key="issue.message">{{issue.message}}</p><button class="button quiet full" @click="selected=repair.id">{{selected===repair.id?'正在预览':'查看路线与时刻'}}</button></article></div>
   <button v-if="selected" class="text-button" @click="selected=''">返回条件修改后的原顺序</button>
   <p class="search-evidence">已检查 {{result.search.evaluated}} / {{result.search.maxEvaluations}} 个候选，复用整日路线，仅查询 {{result.search.roadQueries}} 种路线。{{result.note}}</p>
   <section class="branch-save"><h3>保留这一种可能</h3><p class="form-hint">只有核验可行的安排能保存；分支绑定原计划版本 {{trip.revision}}，不会覆盖原计划。</p><label>分支名称<input v-model="label" maxlength="50"/></label><button class="button primary" @click="save" :disabled="saving||busy||!preview?.feasible||!label.trim()">{{saving?'正在保存…':'保存为独立情景分支'}}</button></section>
   <button class="text-button" @click="showReplay=!showReplay">{{showReplay?'收起执行回放':'回放这次推演的真实执行过程'}}</button><AgentReplay v-if="showReplay" :run-id="result.runId"/>
  </template>
  <section class="branch-library"><h3>已保存的情景分支 <small>{{branches.length}} / 20</small></h3><p v-if="!branches.length" class="form-hint">保存后可跨设备重新打开，或与当前预览并排比较。</p><article v-for="branch in branches" :key="branch.id"><div><strong>{{branch.label}}</strong><small>来源版本 {{branch.sourceRevision}} · {{new Date(branch.created*1000).toLocaleString('zh-CN')}}</small></div><button class="button quiet small" @click="compare(branch)" :disabled="comparisonBusy||saving">并排比较</button><button class="button quiet small" @click="open(branch)" :disabled="saving">独立打开</button><button class="text-button" @click="remove(branch)" :disabled="saving">删除分支</button></article></section>
  <div v-if="comparing" class="branch-compare" aria-label="独立情景并排比较"><article v-for="(item,i) in [preview,comparing]" :key="i"><h3>{{i?compareLabel:'当前推演'}}</h3><p class="form-hint">底色标出另一方案中缺少或停留不同的地点。</p><template v-if="item"><p>步行 {{item.metrics.walkingKm?.toFixed(1)??'未知'}} km · 交通 {{item.metrics.travelMinutes}} 分钟</p><p>停留 {{item.metrics.visitMinutes}} 分钟 · 空余 {{item.metrics.freeMinutes}} 分钟</p><section v-for="(d,di) in item.trip.days" :key="d.date"><strong>Day {{di+1}} · {{time(d.start)}} 出发</strong><p v-for="a in d.activities" :key="a.id" :class="{different:(i?preview:comparing)?.trip.days[di]?.activities.find(x=>x.id===a.id)?.duration!==a.duration}">{{name(a.placeId)}} · {{a.duration}} 分钟</p></section><p>{{item.feasible?'当前道路检查通过':'仍有冲突'}}</p></template><p v-else>请先核验当前推演，再比较。</p></article></div>
 </section>
</template>

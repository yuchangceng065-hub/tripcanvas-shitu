<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted,nextTick} from 'vue'
import {LoaderCircle,Check,AlertTriangle,Sparkles} from '@lucide/vue'
import {startAgent,watchRun,confirmTrip,answerConversation,type ConversationView,type ConversationAnswer,type AgentEvent} from '../api'
import {handleComposerKeydown} from '../composerKeyboard'
import {ApiError} from '../apiError'
import {durationLabel,type PlanMetrics} from '../planningOptions'
import {placeById,registerPlaces,time,type Requirements,type Trip} from '../domain'
const props=defineProps<{title:string;requirements:Requirements;candidateIds:string[];optionalIds?:string[];loggedIn:boolean;initialRunId?:string}>()
const emit=defineEmits<{created:[trip:Trip]}>()
interface PreviewRow {activityId:string;placeId:string;start:number;end:number;travelMinutes:number;waitingMinutes:number;duration:number;lock:string;fixedStart:number|null}
interface AgentPlan {trip:Trip;issues:{severity:string;message:string}[];metrics:PlanMetrics;summary:string;timeline:{date:string;start:number;end:number;activities:PreviewRow[]}[];includedIds:string[];omittedIds:string[];routeSources:string[]}
const events=ref<AgentEvent[]>([]),status=ref('connecting'),error=ref(''),busy=ref(false),plan=ref<AgentPlan|null>(null),runId=ref('')
const conversation=ref<ConversationView|null>(null),replyText=ref(''),chosen=ref<Record<string,string>>({})
const history=ref<HTMLDivElement|null>(null)
watch(()=>conversation.value?.messages.length,async()=>{await nextTick();if(history.value)history.value.scrollTop=history.value.scrollHeight})
let pendingAnswer:ConversationAnswer|undefined
let controller:AbortController|undefined
const pendingKey='tripcanvas.agent.pending.v1'
const signature=computed(()=>JSON.stringify([props.title,props.requirements,props.candidateIds,props.optionalIds??[],props.initialRunId??null]))
const choicesDirty=computed(()=>conversation.value?.mentions.some(m=>(chosen.value[m.key]??'')!==(m.selectedId??''))??false)
const canReply=computed(()=>!!replyText.value.trim()||choicesDirty.value)
const replyExhausted=computed(()=>conversation.value?.remainingReplies===0||(conversation.value?.phase==='agent'&&conversation.value.remainingSteps===0))
const hardErrors=computed(()=>plan.value?.issues.some(issue=>issue.severity==='error')??false)
const statusLabels:Record<string,string>={connecting:'准备连接',running:'运行中…',completed:'已完成',failed:'运行失败',needs_input:'需要补充',needs_confirmation:'请核对需求',gave_up:'未找到方案',incomplete:'尚未完成',interrupted:'运行已中断',stale:'需求已修改',disconnected:'连接已中断'}
const tools:Record<string,string>={search_places:'查找地点',repair_plan:'搜索可行修复',route_day:'查询道路',validate_plan:'核验安排',compare_plans:'比较方案',propose_plan:'整理最终方案',need_input:'请求补充',give_up:'说明不可行原因'}
const graphLabels:Record<string,string>={understand:'理解需求',search_places:'地点搜索',review_requirements:'需求核对',choose_action:'选择工具',execute_tool:'处理动作'}
const graphNodes=computed(()=>events.value.filter(event=>event.phase==='graph_node'))
const visibleEvents=computed(()=>events.value.filter(event=>event.phase!=='graph_node'))
const name=(id:string)=>placeById(id)?.name??id
function label(event:AgentEvent){
 if(event.phase==='graph_pause')return '检查点已保存，等待你的确认'
 if(event.phase==='graph_resume')return '已从 LangGraph 检查点恢复'
 if(event.phase==='mcp_call')return `MCP · ${tools[String(event.detail.tool)]??'规划工具'} · ${event.status==='error'?'发现问题':'已返回'}`
 if(event.phase==='model')return `第 ${event.detail.step??'?'} 步 · ${tools[String(event.detail.action)]??'选择下一步'}`
 if(event.phase==='tool_call')return `正在${tools[String(event.detail.tool)]??'检查方案'}`
 if(event.phase==='tool_result')return event.status==='error'||event.detail.ok===false?'本次检查未通过，继续调整':'已取得检查结果'
 if(event.phase==='understanding')return '理解你的补充条件'
 if(event.phase==='place_search')return '查找真实地点'
 if(event.phase==='clarification')return '整理需要确认的信息'
 if(event.phase==='resumed')return '接回原任务，继续处理'
 return statusLabels[event.phase]??(event.phase==='model_error'?'模型输出需要重试':'处理行程')
}
function summary(event:AgentEvent){
 if(event.phase==='mcp_call'&&typeof event.detail.durationMs==='number')return `${event.detail.durationMs} ms · Streamable HTTP`
 if(typeof event.detail.error==='string')return event.detail.error
 if(event.detail.truncated)return '记录较长，已显示摘要。'
 const value=event.detail.summary as {feasible?:boolean;minutes?:number}|undefined
 if(value?.feasible!==undefined)return value.feasible?'道路与时间条件通过检查':'方案仍有冲突'
 if(typeof value?.minutes==='number')return `道路转场 ${durationLabel(value.minutes)}`
 return ''
}
function clearPending(){try{const value=JSON.parse(sessionStorage.getItem(pendingKey)||'null');if(value?.runId===runId.value)sessionStorage.removeItem(pendingKey)}catch{}}
function previewComplete(value:AgentPlan){
 return value?.trip&&Array.isArray(value.timeline)&&value.timeline.length===value.trip.days.length&&value.timeline.every((day,i)=>
  day.date===value.trip.days[i]?.date&&day.activities.length===value.trip.days[i]?.activities.length&&day.activities.every((row,j)=>
   row.activityId===value.trip.days[i]?.activities[j]?.id&&row.placeId===value.trip.days[i]?.activities[j]?.placeId&&Number.isFinite(row.start)&&Number.isFinite(row.end)&&row.end>=row.start))
}
async function observe(){
 controller?.abort();const active=new AbortController();controller=active;busy.value=true;error.value='';status.value='running'
 try{
  const run=await watchRun(runId.value,event=>{if(!active.signal.aborted)events.value.push(event)},active.signal,events.value.at(-1)?.seq??0)
  if(active.signal.aborted)return
  status.value=String(run.status??'unknown')
  conversation.value=run.conversation??null
  if(conversation.value){registerPlaces(conversation.value.mentions.flatMap(m=>m.candidates));chosen.value=Object.fromEntries(conversation.value.mentions.filter(m=>m.selectedId).map(m=>[m.key,m.selectedId!]))}
  if(run.status==='completed'){
   const result=run.validation as AgentPlan
   if(!previewComplete(result))throw new Error('本次结果缺少完整逐站预览，请重新运行后再保存。')
   registerPlaces(result.trip.placeSnapshots);plan.value=result
  }
  if(run.status==='failed')error.value=String(run.error??'运行失败')
  if(run.status==='needs_input'&&!conversation.value)error.value=String(run.question??'需要补充信息')
  if(['gave_up','incomplete','interrupted'].includes(run.status))error.value=String(run.message??run.error??'本次运行已结束，请调整需求后重试。')
  if(!['needs_input','needs_confirmation','failed','interrupted'].includes(run.status))clearPending()
 }catch(e){if(!active.signal.aborted){status.value='disconnected';error.value=(e as Error).message}}
 finally{if(!active.signal.aborted)busy.value=false}
}
function answerKey(event:KeyboardEvent){handleComposerKeydown(event,text=>{replyText.value=text;void sendAnswer()})}
async function sendAnswer(confirm=false,retry=false){
 if(busy.value||!conversation.value)return
 const value:ConversationAnswer={version:conversation.value.version,requestId:'',text:confirm||retry?'':replyText.value.trim(),choices:confirm||retry||conversation.value.phase==='agent'?{}:{...chosen.value},confirm,retry}
 const identity=JSON.stringify({...value,requestId:''})
 if(!pendingAnswer||JSON.stringify({...pendingAnswer,requestId:''})!==identity)pendingAnswer={...value,requestId:crypto.randomUUID()}
 const previousStatus=status.value
 busy.value=true;status.value='running';error.value='';plan.value=null
 try{
  await answerConversation(runId.value,pendingAnswer)
  pendingAnswer=undefined;replyText.value='';await observe()
 }catch(e){
  busy.value=false;status.value=previousStatus;error.value=(e as Error).message
  if(e instanceof ApiError&&e.status===409){pendingAnswer=undefined;await observe();error.value='任务已有新回复，已更新当前信息，请核对后再提交。'}
 }
}
async function start(){
 if(busy.value)return
 if(!props.loggedIn){error.value='请先登录再使用 Agent';status.value='failed';return}
 if(props.candidateIds.length<props.requirements.days){error.value='请先确认足够的候选地点';status.value='failed';return}
 busy.value=true;error.value='';events.value=[];plan.value=null;status.value='running';runId.value=''
 const active=new AbortController();controller=active
 try{
  const started=await startAgent({title:props.title,requirements:props.requirements,candidateIds:props.candidateIds,optionalIds:props.optionalIds??[],useModel:false},active.signal)
  if(active.signal.aborted)return
  runId.value=started.runId
  try{sessionStorage.setItem(pendingKey,JSON.stringify({runId:runId.value,signature:signature.value}))}catch{}
  await observe()
 }catch(e){if(!active.signal.aborted){status.value='failed';error.value=(e as Error).message;busy.value=false}}
}
async function save(){
 if(!plan.value||busy.value||hardErrors.value)return
 busy.value=true;error.value=''
 try{const result=await confirmTrip(plan.value.trip,0);emit('created',result.trip)}
 catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
watch(signature,()=>{controller?.abort();busy.value=false;status.value='stale';error.value='需求或候选已修改，请重新运行以核验新安排。';plan.value=null;runId.value='';events.value=[]})
onMounted(()=>{
 if(props.initialRunId){runId.value=props.initialRunId;void observe();return}
 try{const pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');if(props.loggedIn&&pending?.signature===signature.value&&typeof pending.runId==='string'){runId.value=pending.runId;void observe();return}}catch{}
 void start()
})
watch(()=>props.loggedIn,value=>{if(!value){controller?.abort();conversation.value=null;plan.value=null;busy.value=false;error.value='请登录后继续本任务。'}})
onUnmounted(()=>controller?.abort())
</script>
<template>
 <section class="agent-trace" aria-label="Agent 运行轨迹">
  <header><span class="agent-title"><Sparkles :size="15"/>Agent 运行轨迹</span><small role="status">{{statusLabels[status]??status}}</small></header>
  <p v-if="busy" class="form-hint"><LoaderCircle :size="13" class="spin"/>正在连接本次运行，断线后会继续获取结果…</p>
  <div v-if="graphNodes.length" class="agent-graph-track" aria-label="LangGraph 节点轨迹">
   <strong>LangGraph · MCP</strong>
   <ol><li v-for="node in graphNodes" :key="node.seq">{{graphLabels[String(node.detail.node)]||node.detail.node}}</li></ol>
  </div>
  <ol class="agent-steps"><li v-for="event in visibleEvents" :key="event.seq" :class="event.status"><span>{{label(event)}}</span><small v-if="summary(event)">{{summary(event)}}</small></li></ol>
  <p v-if="error" class="form-error" role="alert"><AlertTriangle :size="13"/>{{error}}</p>
  <button v-if="status==='disconnected'&&runId" class="button quiet" @click="observe" :disabled="busy">继续查看本次运行</button>
  <section v-if="conversation&&!busy&&['needs_input','needs_confirmation','failed','interrupted'].includes(status)" class="agent-conversation" aria-label="继续原任务">
   <div ref="history" class="conversation-history" role="log" aria-live="polite" aria-label="对话记录"><p v-for="(message,i) in conversation.messages" :key="i" :class="message.role"><strong>{{message.role==='user'?'你':'旅行搭子'}}</strong>{{message.text}}</p></div>
   <div v-if="conversation.phase==='intake'" class="conversation-facts">
    <h4>已整理的需求</h4>
    <p>{{conversation.requirements.city||'城市待补充'}} · {{conversation.requirements.startDate||'日期待补充'}} · {{conversation.requirements.days??'待确认'}} 天 · {{conversation.requirements.people??'待确认'}} 人 · {{conversation.requirements.mode==='walk'?'步行':conversation.requirements.mode==='drive'?'自驾接驳':'交通待确认'}}</p>
    <p v-if="conversation.canConfirm">每天 {{time(conversation.requirements.start!)}}—{{time(conversation.requirements.end!)}}<template v-if="conversation.requirements.returnBy!=null"> · 末日最晚 {{time(conversation.requirements.returnBy)}} 到达返程点</template></p>
    <fieldset v-for="mention in conversation.mentions" :key="mention.key" class="conversation-place">
     <legend>{{mention.role==='origin'?'出发点':mention.role==='destination'?'末日终点':mention.required?'必去':'可选'}} · {{mention.text}}</legend>
     <label v-for="place in mention.candidates" :key="place.id"><input type="radio" :name="mention.key" :value="place.id" v-model="chosen[mention.key]"/><span><strong>{{place.name}}</strong><small>{{place.address||'请核对具体位置'}} · {{place.provider==='amap'?'高德':place.provider==='photon'?'Photon':'地点目录'}}</small></span></label>
     <p v-if="!mention.candidates.length" class="form-hint">{{mention.error||'没有找到结果，请补充更具体的名称。'}}</p>
    </fieldset>
   </div>
   <p v-for="note in conversation.notes" :key="note" class="form-hint">待核对：{{note}}</p>
   <p v-if="conversation.canConfirm" class="form-hint">以上条件和具体地点需要你确认；未写的每日时间暂按展示值安排。确认后才查询道路并规划。</p>
   <form v-if="['needs_input','needs_confirmation'].includes(status)" @submit.prevent="sendAnswer()">
    <label>补充回答<textarea v-model="replyText" @keydown="answerKey" maxlength="1000" rows="3" placeholder="例如：两个人，步行；先去湖边，丝绸馆可选。" :disabled="busy"/></label>
    <p class="form-hint">选词后 Enter 发送 · Shift+Enter 换行</p>
    <button class="button quiet" type="submit" :disabled="busy||!canReply||replyExhausted">提交补充，继续同一任务</button>
   </form>
   <p v-if="conversation.canConfirm&&canReply" class="form-hint">有尚未提交的补充或地点变更，请先提交，再确认。</p>
   <button v-if="conversation.canConfirm" class="button primary full" @click="sendAnswer(true)" :disabled="busy||canReply||conversation.remainingReplies===0">确认需求与地点，继续规划</button>
   <button v-if="['failed','interrupted'].includes(status)" class="button quiet" @click="sendAnswer(false,true)" :disabled="busy||replyExhausted">重试本任务</button>
   <p class="form-hint">还可补充 {{conversation.remainingReplies}} 次 · 规划还可决策 {{conversation.remainingSteps}} 步。必去、返程和时间硬约束不会因自由回答而自动解除。</p>
  </section>
  <div v-if="plan" class="agent-plan" aria-label="Agent 方案完整预览">
   <h3>{{plan.trip.title}}</h3>
   <p class="form-hint">{{plan.trip.people}} 人 · {{plan.trip.mode==='walk'?'步行':'自驾与步行接驳'}} · {{plan.trip.days.length}} 天</p>
   <p class="form-hint">{{plan.summary}}</p>
   <dl class="agent-metrics">
    <div><dt>转场步行</dt><dd>{{plan.metrics.walkingKm===null?'未能拆分':plan.metrics.walkingKm.toFixed(1)+' km'}}</dd></div>
    <div><dt>交通与接驳</dt><dd>{{durationLabel(plan.metrics.travelMinutes)}}</dd></div>
    <div><dt>游览停留</dt><dd>{{durationLabel(plan.metrics.visitMinutes)}}</dd></div>
    <div><dt>可支配空余</dt><dd>{{durationLabel(plan.metrics.freeMinutes)}}</dd></div>
   </dl>
   <p class="form-hint">道路来源：{{plan.routeSources.map(source=>source==='amap'?'高德':source==='osrm'?'OSRM':source).join('、')||'单地点无需转场'}}。空余尚未安排用餐；景区内部步行、营业与预约仍需确认。</p>
   <section v-for="(day,di) in plan.timeline" :key="day.date" class="agent-day" :aria-label="`Day ${di+1} 逐站安排`">
    <h4>Day {{di+1}} · {{day.date}} <small>{{time(day.start)}}—{{time(day.end)}}</small></h4>
    <ol>
     <li v-for="(row,index) in day.activities" :key="row.activityId">
      <span class="agent-stop-index">{{index+1}}</span>
      <div><strong>{{name(row.placeId)}}</strong><small>{{row.duration}} 分钟{{row.lock==='fixed'?' · 固定 '+time(row.fixedStart!):row.lock==='keep'?' · 必须保留':' · 可选地点'}}<template v-if="index"> · 转场 {{durationLabel(row.travelMinutes)}}</template><template v-if="row.waitingMinutes"> · 等待 {{durationLabel(row.waitingMinutes)}}</template></small></div>
      <span class="agent-stop-time">{{time(row.start)}}—{{time(row.end)}}</span>
     </li>
    </ol>
    <p class="form-hint">当天转场步行 {{plan.metrics.days[di]?.walkingKm==null?'未知':plan.metrics.days[di]!.walkingKm!.toFixed(1)+' km'}} · 可支配空余 {{durationLabel(plan.metrics.days[di]?.freeMinutes??0)}}</p>
   </section>
   <p class="agent-tradeoff"><strong>地点取舍：</strong>{{plan.omittedIds.length?'舍弃可选地点：'+plan.omittedIds.map(name).join('、'):'保留全部候选地点'}}</p>
   <p v-for="issue in plan.issues" :key="issue.message" :class="issue.severity==='error'?'form-error':'form-hint'">{{issue.severity==='warning'?'提醒':'冲突'}}：{{issue.message}}</p>
   <p class="form-hint">确认后将重新核验道路与时间，再保存这份安排。</p>
   <button class="button primary" @click="save" :disabled="busy||hardErrors"><Check :size="15"/>确认并保存 Agent 方案</button>
  </div>
  <button v-if="!initialRunId" class="text-button" @click="start" :disabled="busy">重新运行</button>
 </section>
</template>

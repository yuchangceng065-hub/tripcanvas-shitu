<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {LoaderCircle,Check,AlertTriangle,Sparkles} from '@lucide/vue'
import {api,startAgent,streamRun,confirmTrip,type AgentEvent} from '../api'
import {durationLabel} from '../planningOptions'
import type {Requirements,Trip} from '../domain'
const props=defineProps<{title:string;requirements:Requirements;candidateIds:string[];optionalIds?:string[];loggedIn:boolean}>()
const emit=defineEmits<{created:[trip:Trip]}>()
const events=ref<AgentEvent[]>([]),status=ref('connecting'),error=ref(''),busy=ref(false),plan=ref<{trip:Trip;issues:{severity:string;message:string}[];metrics:Record<string,number|null>;summary:string}|null>(null)
const controller=new AbortController()
function label(event:AgentEvent){
 if(event.phase==='model')return `第 ${event.detail.step??'?'} 步 · 选择 ${event.detail.action??'动作'}`
 if(event.phase==='tool_call')return `调用 ${event.detail.tool}${event.detail.step?'（第 '+event.detail.step+' 步）':''}`
 if(event.phase==='tool_result')return event.detail.ok===false?'工具返回错误':'工具返回结果'
 if(event.phase==='completed')return '完成'
 if(event.phase==='failed')return '失败'
 if(event.phase==='needs_input')return '需要你确认'
 return event.phase
}
async function start(){
 if(!props.loggedIn){error.value='请先登录再使用 Agent';status.value='failed';return}
 busy.value=true;error.value='';events.value=[];plan.value=null;status.value='running'
 try{
  const started=await startAgent({title:props.title,requirements:props.requirements,candidateIds:props.candidateIds,optionalIds:props.optionalIds??[],useModel:false})
  await streamRun(started.runId,event=>events.value.push(event),controller.signal)
  const run=await api('/runs/'+started.runId)
  status.value=String(run.status??'unknown')
  if(run.status==='completed'&&run.validation)plan.value=run.validation
  if(run.status==='failed')error.value=String(run.error??'运行失败')
  if(run.status==='needs_input')error.value=String(run.question??'需要补充信息')
  if(run.status==='gave_up'||run.status==='incomplete')error.value=String(run.message??'未完成')
 }catch(e){if(!controller.signal.aborted){status.value='failed';error.value=(e as Error).message}}
 finally{busy.value=false}
}
async function save(){
 if(!plan.value||busy.value)return
 busy.value=true;error.value=''
 try{const result=await confirmTrip(plan.value.trip,0);emit('created',result.trip)}
 catch(e){error.value=(e as Error).message}
 finally{busy.value=false}
}
onMounted(start);onUnmounted(()=>controller.abort())
</script>
<template>
 <section class="agent-trace" aria-label="Agent 运行轨迹">
  <header><span class="agent-title"><Sparkles :size="15"/>Agent 运行轨迹</span><small>{{status==='running'?'运行中…':status}}</small></header>
  <p v-if="busy&&!events.length" class="form-hint"><LoaderCircle :size="13" class="spin"/>正在启动模型与工具…</p>
  <ol class="agent-steps"><li v-for="event in events" :key="event.seq" :class="event.status"><span>{{label(event)}}</span><small v-if="event.phase==='tool_result'&&event.detail.summary">{{JSON.stringify(event.detail.summary).slice(0,120)}}</small></li></ol>
  <p v-if="error" class="form-error" role="alert"><AlertTriangle :size="13"/>{{error}}</p>
  <div v-if="plan" class="agent-plan">
   <p class="form-hint">{{plan.summary}}</p>
   <p class="form-hint">步行 {{plan.metrics.walkingKm===null?'未能拆分':plan.metrics.walkingKm+' km'}} · 交通 {{durationLabel(Number(plan.metrics.travelMinutes??0))}} · 游览 {{durationLabel(Number(plan.metrics.visitMinutes??0))}}</p>
   <p v-for="issue in plan.issues" :key="issue.message" class="form-hint">{{issue.severity==='warning'?'提醒':'冲突'}}：{{issue.message}}</p>
   <button class="button primary" @click="save" :disabled="busy"><Check :size="15"/>确认并保存 Agent 方案</button>
  </div>
  <button class="text-button" @click="start" :disabled="busy">重新运行</button>
 </section>
</template>

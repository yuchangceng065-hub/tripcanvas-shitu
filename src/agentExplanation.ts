import type {AgentEvent} from './api'
import {placeById,type Requirements} from './domain'
import {durationLabel,type PlanMetrics} from './planningOptions'

interface ExplainedPlan {
 includedIds:string[]
 omittedIds:string[]
 metrics:PlanMetrics
 timeline:{activities:{placeId:string;start:number;waitingMinutes:number}[]}[]
}

// Only observed tool results supply facts. Model-written explanations are
// displayed separately from this summary.
export function planningEvidence(events:AgentEvent[]):string[]{
 const lines:string[]=[]
 for(const event of events){
  if(event.phase!=='tool_result')continue
  const summaries=Array.isArray(event.detail.summary)?event.detail.summary:[event.detail.summary]
  for(const summary of summaries){
   if(!summary||typeof summary!=='object')continue
   const value=summary as Record<string,unknown>
   if(Array.isArray(value.issues))for(const issue of value.issues){
    if(issue&&issue.severity==='error'&&typeof issue.message==='string')lines.push('曾发现冲突 · '+issue.message)
   }
   if(typeof value.minutes==='number'&&Number.isFinite(value.minutes)){
    lines.push('道路查询结果 · 转场 '+durationLabel(value.minutes)+(typeof value.walkingKm==='number'?'，其中步行 '+value.walkingKm.toFixed(1)+' km':''))
   }
   if(value.feasible===true)lines.push('一次方案检查通过道路与时间约束。')
  }
 }
 return [...new Set(lines)]
}

// `requiredIds` / `requirements` describe the FINAL confirmed requirements.
// Passing null means the final plan carries no such evidence; in that case no
// must-visit or return-margin claim is emitted rather than substituting the
// form values captured before a multi-turn conversation.
export function planDecision(plan:ExplainedPlan,requiredIds:string[]|null,requirements:Requirements|null):string[]{
 const name=(id:string)=>placeById(id)?.name??id
 const lines:string[]=[]
 if(requiredIds?.length&&requiredIds.every(id=>plan.includedIds.includes(id))){
  lines.push('最终方案保留必去地点 · '+requiredIds.map(name).join('、'))
 }
 if(plan.omittedIds.length)lines.push('最终未安排的可选地点 · '+plan.omittedIds.map(name).join('、'))
 lines.push('已核验方案的交通与接驳共 '+durationLabel(plan.metrics.travelMinutes)+'，可支配空余 '+durationLabel(plan.metrics.freeMinutes)+'。')
 const destination=requirements?.destinationPlaceId,deadline=requirements?.returnBy
 const last=plan.timeline.at(-1)?.activities.at(-1)
 if(destination&&deadline!=null&&last?.placeId===destination&&Number.isFinite(last.start)&&Number.isFinite(last.waitingMinutes)){
  const margin=deadline-(last.start-last.waitingMinutes)
  lines.push(margin>=0?'按当前道路估时，到达返程点比期限提前 '+margin+' 分钟。':'按当前道路估时，到达返程点比期限晚 '+(-margin)+' 分钟。')
 }
 return lines
}

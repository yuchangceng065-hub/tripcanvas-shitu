import {expect,it} from 'vitest'
import {planningEvidence,planDecision} from './agentExplanation'
import type {Requirements} from './domain'
const req:Requirements={city:'杭州',startDate:'2026-11-14',days:1,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[],destinationPlaceId:'station',returnBy:1020}
it('解释来自实际工具冲突和道路结果，模型文字不充当证据',()=>{
 const lines=planningEvidence([
  {seq:1,phase:'model',status:'ok',detail:{summary:{issues:[{severity:'error',message:'模型猜测'}]}}},
  {seq:2,phase:'tool_result',status:'ok',detail:{summary:{minutes:25,walkingKm:1.2}}},
  {seq:3,phase:'tool_result',status:'ok',detail:{summary:{feasible:false,issues:[{severity:'error',message:'比返程期限晚 20 分钟'}]}}},
  {seq:4,phase:'tool_result',status:'ok',detail:{summary:{feasible:true}}},
 ])
 expect(lines.join(' ')).toContain('晚 20 分钟');expect(lines.join(' ')).toContain('1.2 km')
 expect(lines.join(' ')).not.toContain('模型猜测')
})
it('返程余量扣除提前到达后的等待，并如实展示最终取舍',()=>{
 const plan={includedIds:['quyuan'],omittedIds:['coffee'],metrics:{travelMinutes:40,freeMinutes:60} as never,timeline:[{activities:[{placeId:'station',start:1020,waitingMinutes:15}]}]}
 const lines=planDecision(plan,['quyuan'],req).join(' ')
 expect(lines).toContain('保留必去地点');expect(lines).toContain('最终未安排的可选地点')
 expect(lines).toContain('提前 15 分钟')
 expect(planDecision(plan,['museum'],req).join(' ')).not.toContain('保留必去地点')
})
it('多轮对话后的解释只采用最终确认的必去地点和返程期限',()=>{
 const plan={includedIds:['quyuan','museum'],omittedIds:['silk'],metrics:{travelMinutes:40,freeMinutes:60} as never,timeline:[{activities:[{placeId:'quyuan',start:570,waitingMinutes:0},{placeId:'station',start:1005,waitingMinutes:15}]}]}
 const initial:Requirements={...req,mustVisitIds:['quyuan'],returnBy:1020}
 const confirmed:Requirements={...initial,mustVisitIds:['quyuan','museum'],returnBy:1000}
 const lines=planDecision(plan,confirmed.mustVisitIds!,confirmed).join(' ')
 expect(lines).toContain('曲院风荷');expect(lines).toContain('浙江省博物馆')
 expect(lines).toContain('提前 10 分钟')
 expect(lines).not.toContain('提前 30 分钟')
 // Substituting the form values captured before the clarification would be a
 // different (and unverified) explanation.
 expect(planDecision(plan,initial.mustVisitIds!,initial)).not.toEqual(planDecision(plan,confirmed.mustVisitIds!,confirmed))
})
it('最终方案缺少需求证据时不编造必去与返程结论',()=>{
 const plan={includedIds:['quyuan'],omittedIds:['coffee'],metrics:{travelMinutes:40,freeMinutes:60} as never,timeline:[{activities:[{placeId:'station',start:1020,waitingMinutes:15}]}]}
 const lines=planDecision(plan,null,null).join(' ')
 expect(lines).not.toContain('保留必去地点')
 expect(lines).not.toContain('返程点')
 expect(lines).toContain('最终未安排的可选地点')
 expect(lines).toContain('可支配空余')
})

// @vitest-environment happy-dom
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createApp,nextTick,h,reactive,type App} from 'vue'
import AgentTrace from './components/AgentTrace.vue'
import {startAgent,watchRun,confirmTrip} from './api'
import type {Requirements,Trip} from './domain'
vi.mock('./api',()=>({startAgent:vi.fn(),watchRun:vi.fn(),confirmTrip:vi.fn()}))
const startMock=vi.mocked(startAgent),watchMock=vi.mocked(watchRun),confirmMock=vi.mocked(confirmTrip)
const requirements:Requirements={city:'杭州',startDate:'2026-11-14',days:1,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[]}
const trip=():Trip=>({id:'agent-qa',title:'测试杭州',city:'杭州',people:2,mode:'walk',revision:1,days:[{date:'2026-11-14',start:570,end:1110,activities:[{id:'a',placeId:'quyuan',duration:90,lock:'keep'},{id:'b',placeId:'museum',duration:90,lock:'keep'}]}]})
const plan=()=>({trip:trip(),issues:[],metrics:{walkingKm:.6,travelMinutes:10,visitMinutes:180,freeMinutes:350,days:[{walkingKm:.6,freeMinutes:350}]},summary:'两站慢走',timeline:[{date:'2026-11-14',start:570,end:1110,activities:[{activityId:'a',placeId:'quyuan',start:570,end:660,travelMinutes:0,waitingMinutes:0,duration:90,lock:'keep',fixedStart:null},{activityId:'b',placeId:'museum',start:670,end:760,travelMinutes:10,waitingMinutes:0,duration:90,lock:'keep',fixedStart:null}]}],includedIds:['quyuan','museum'],omittedIds:['silk'],routeSources:['amap']})
let app:App,host:HTMLDivElement,created:Trip|null,input:{title:string;requirements:Requirements;candidateIds:string[];optionalIds:string[];loggedIn:boolean}
const flush=async()=>{for(let i=0;i<4;i++){await new Promise(r=>setTimeout(r,0));await nextTick()}}
const button=(text:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent?.includes(text))!
function mount(){app=createApp({render:()=>h(AgentTrace,{...input,onCreated:(value:Trip)=>{created=value}})});app.mount(host)}
beforeEach(()=>{
 vi.resetAllMocks();sessionStorage.clear();created=null;host=document.createElement('div');document.body.append(host)
 input=reactive({title:'测试杭州',requirements:{...requirements},candidateIds:['quyuan','museum','silk'],optionalIds:['silk'],loggedIn:true})
 startMock.mockResolvedValue({runId:'r1',status:'running'})
 watchMock.mockImplementation(async(_id,onEvent)=>{
  onEvent({seq:1,phase:'tool_call',status:'ok',detail:{step:1,tool:'route_day'}})
  onEvent({seq:2,phase:'completed',status:'ok',detail:{}})
  return {status:'completed',validation:plan()}
 })
 confirmMock.mockResolvedValue({trip:trip(),revision:1,issues:[],idempotent:false})
})
afterEach(()=>{app?.unmount();host.remove();sessionStorage.clear()})
it('显示真实逐站时间、锁定、来源和地点取舍，明确确认才保存',async()=>{
 mount();await flush()
 expect(host.textContent).toContain('正在查询道路')
 expect(host.textContent).toContain('曲院风荷');expect(host.textContent).toContain('浙江省博物馆')
 expect(host.textContent).toContain('11:10—12:40');expect(host.textContent).toContain('2026-11-14')
 expect(host.textContent).toContain('必须保留');expect(host.textContent).toContain('舍弃可选地点：中国丝绸博物馆')
 expect(host.textContent).toContain('高德');expect(confirmMock).not.toHaveBeenCalled()
 expect(startMock).toHaveBeenCalledWith(expect.objectContaining({title:'测试杭州',optionalIds:['silk']}),expect.any(AbortSignal))
 button('确认并保存 Agent 方案').click();await flush()
 expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({id:'agent-qa',title:'测试杭州'}),0)
 expect(created).toBeTruthy()
})
it('连接失败后继续查看相同运行，不重新创建或消耗生成次数',async()=>{
 watchMock.mockRejectedValueOnce(new Error('连接中断'))
 mount();await flush();expect(button('继续查看本次运行')).toBeTruthy()
 button('继续查看本次运行').click();await flush()
 expect(startMock).toHaveBeenCalledTimes(1);expect(watchMock).toHaveBeenCalledTimes(2)
 expect(watchMock.mock.calls.every(args=>args[0]==='r1')).toBe(true)
 expect(host.textContent).toContain('测试杭州');expect(confirmMock).not.toHaveBeenCalled()
})
it('重新打开相同需求时恢复保存的运行编号',async()=>{
 sessionStorage.setItem('tripcanvas.agent.pending.v1',JSON.stringify({runId:'saved-run',signature:JSON.stringify([input.title,input.requirements,input.candidateIds,input.optionalIds])}))
 mount();await flush();expect(startMock).not.toHaveBeenCalled();expect(watchMock.mock.calls[0]?.[0]).toBe('saved-run')
})
it('需求变更使旧方案失效，不能继续保存旧结果',async()=>{
 mount();await flush();expect(button('确认并保存 Agent 方案')).toBeTruthy()
 input.candidateIds=['quyuan','museum'];await flush()
 expect(button('确认并保存 Agent 方案')).toBeUndefined();expect(host.textContent).toContain('需求或候选已修改')
 expect(confirmMock).not.toHaveBeenCalled()
})
it('缺少逐站时间或存在硬冲突时不能确认保存',async()=>{
 watchMock.mockResolvedValueOnce({status:'completed',validation:{...plan(),timeline:[]}})
 mount();await flush();expect(button('确认并保存 Agent 方案')).toBeUndefined()
 expect(host.textContent).toContain('缺少完整逐站预览')
 watchMock.mockResolvedValueOnce({status:'completed',validation:{...plan(),issues:[{severity:'error',message:'返程晚到'}]}})
 button('重新运行').click();await flush();expect(button('确认并保存 Agent 方案').disabled).toBe(true)
})

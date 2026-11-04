// @vitest-environment happy-dom
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createApp,nextTick,type App} from 'vue'
import AgentTrace from './components/AgentTrace.vue'
import {api,startAgent,streamRun,confirmTrip} from './api'
import type {Trip} from './domain'
vi.mock('./api',()=>({api:vi.fn(),startAgent:vi.fn(),streamRun:vi.fn(),confirmTrip:vi.fn()}))
const apiMock=vi.mocked(api),startMock=vi.mocked(startAgent),streamMock=vi.mocked(streamRun),confirmMock=vi.mocked(confirmTrip)
const requirements={city:'杭州',startDate:'2026-11-14',days:1,people:2,mode:'walk',start:570,end:1110,pace:'balanced',interests:[]} as const
const trip=():Trip=>({id:'agent-qa',title:'Agent',city:'杭州',people:2,mode:'walk',revision:1,days:[{date:'2026-11-14',start:570,end:1110,activities:[{id:'a',placeId:'quyuan',duration:90,lock:'none'}]}]})
let app:App,host:HTMLDivElement,created:Trip|null
const flush=async()=>{for(let i=0;i<3;i++){await new Promise(r=>setTimeout(r,0));await nextTick()}}
beforeEach(()=>{
 vi.resetAllMocks();created=null;host=document.createElement('div');document.body.append(host)
 startMock.mockResolvedValue({runId:'r1',status:'running'})
 streamMock.mockImplementation(async(_id,onEvent)=>{
  onEvent({seq:1,phase:'model',status:'ok',detail:{step:1,action:'route_day'}})
  onEvent({seq:2,phase:'tool_call',status:'ok',detail:{step:1,tool:'route_day'}})
  onEvent({seq:3,phase:'tool_result',status:'ok',detail:{ok:true,summary:{minutes:20}}})
  onEvent({seq:4,phase:'completed',status:'ok',detail:{}})
 })
 apiMock.mockResolvedValue({status:'completed',validation:{trip:trip(),issues:[],metrics:{walkingKm:1.2,travelMinutes:20,visitMinutes:90},summary:'两站慢走'}})
 confirmMock.mockResolvedValue({trip:trip(),revision:1,issues:[],idempotent:false})
 app=createApp(AgentTrace,{title:'测试',requirements,candidateIds:['quyuan'],optionalIds:[],loggedIn:true,onCreated:(value:Trip)=>{created=value}})
 app.mount(host)
})
afterEach(()=>{app.unmount();host.remove()})
it('流式展示工具步骤并确认保存',async()=>{
 await flush()
 expect(host.textContent).toContain('调用 route_day')
 expect(host.textContent).toContain('两站慢走')
 const save=[...host.querySelectorAll('button')].find(b=>b.textContent?.includes('确认并保存 Agent 方案'))
 expect(save).toBeTruthy();save!.click();await flush()
 expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({id:'agent-qa'}),0)
 expect(created).toBeTruthy()
})

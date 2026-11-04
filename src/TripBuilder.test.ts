// @vitest-environment happy-dom
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createApp,nextTick,type App} from 'vue'
import TripBuilder from './components/TripBuilder.vue'
import {api,confirmTrip,beginConversation} from './api'
import {ApiError} from './apiError'
import {places,type Trip} from './domain'
vi.mock('./api',()=>({api:vi.fn(),confirmTrip:vi.fn(),connection:{routing:'osrm'},beginConversation:vi.fn()}))
vi.mock('./components/AgentTrace.vue',()=>({default:{render:()=>null}}))
vi.mock('./components/PlacePicker.vue',()=>({default:{render:()=>null}}))
let app:App,host:HTMLDivElement
const apiMock=vi.mocked(api),confirmMock=vi.mocked(confirmTrip)
const flush=async()=>{await new Promise(resolve=>setTimeout(resolve,0));await nextTick()}
const button=(text:string)=>{const found=[...host.querySelectorAll('button')].find(b=>b.textContent?.includes(text));if(!found)throw new Error('Missing button '+text);return found}
const click=async(text:string)=>{button(text).click();await flush()}
const place=(id:string)=>places.find(p=>p.id===id)!
const issue=(action='shorten',message='第一个冲突')=>({code:'day_overflow',severity:'error',message,options:[{action,activityId:'a',day:0,minutes:15}]})
const trip=():Trip=>({id:'component-qa',title:'组件验收',city:'杭州',people:2,mode:'walk',revision:1,days:[{date:'2026-11-14',start:570,end:1110,activities:[{id:'a',placeId:'quyuan',duration:90,lock:'none'}]}]})
const parsed=()=>({requirements:{startDate:'2026-11-14',days:1,people:2},missing:[],evidence:{},notes:[],suggestions:[],mentions:[{text:'校区',role:'visit',candidates:[place('quyuan'),place('baidi')]}]})
beforeEach(()=>{vi.resetAllMocks();sessionStorage.removeItem('tripcanvas.conversation.active.v1');host=document.createElement('div');document.body.append(host);app=createApp(TripBuilder,{loggedIn:true,hasModel:false});app.mount(host)})
afterEach(()=>{app.unmount();host.remove()})
async function chooseAndGenerate(issues:unknown[]=[]){
 apiMock.mockImplementation(async(path)=>{
  if(path==='/planner/requirements')return parsed()
  if(path==='/planner/draft')return {trip:trip(),source:'heuristic',issues,warnings:[],routes:{}}
  throw new Error('Unexpected endpoint '+path)
 })
 const text=host.querySelector('textarea')!;text.value='2026-11-14去杭州';text.dispatchEvent(new Event('input'));await nextTick()
 await click('识别需求');host.querySelector<HTMLButtonElement>('.mention-options button')!.click();await nextTick()
 await click('确认需求');await click('生成草案')
}
it('改选地点后，候选清单只包含最终校区',async()=>{
 apiMock.mockResolvedValue(parsed())
 const text=host.querySelector('textarea')!;text.value='确认校区';text.dispatchEvent(new Event('input'));await nextTick();await click('识别需求')
 const choices=host.querySelectorAll<HTMLButtonElement>('.mention-options button');choices[0]!.click();await nextTick();choices[1]!.click();await nextTick()
 await click('确认需求')
 const selected=host.querySelector('.candidate-list')!.textContent!
 expect(selected).toContain(place('baidi').name);expect(selected).not.toContain(place('quyuan').name)
})
it('有返程时间但未选终点时，时间输入仍可见且可以清空解除限制',async()=>{
 apiMock.mockResolvedValue({...parsed(),mentions:[],requirements:{startDate:'2026-11-14',days:1,people:2,returnBy:1050}})
 const text=host.querySelector('textarea')!;text.value='下午五点半前到站';text.dispatchEvent(new Event('input'));await nextTick();await click('识别需求')
 await click('确认需求');expect(host.textContent).toContain('请确认返程地点和时间')
 const label=[...host.querySelectorAll('label')].find(el=>el.textContent?.includes('末日最晚到达'))!
 const timeInput=label.querySelector('input')!;expect(timeInput.value).toBe('17:30')
 timeInput.value='';timeInput.dispatchEvent(new Event('change'));await nextTick();await click('确认需求')
 expect(host.textContent).toContain('想去的地方');expect(host.querySelector('[role="alert"]')).toBeNull()
})
it('修复只预览，保留剩余问题，全部解决后仍需最终确认才保存',async()=>{
 await chooseAndGenerate([issue()])
 apiMock.mockResolvedValueOnce({trip:trip(),routes:{},issues:[issue('extend_day','还有第二个冲突')]})
 await click('缩短')
 expect(confirmMock).not.toHaveBeenCalled();expect(host.textContent).toContain('还有第二个冲突');expect(button('确认并保存').disabled).toBe(true)
 apiMock.mockResolvedValueOnce({trip:trip(),routes:{},issues:[]})
 await click('当天结束延后')
 expect(confirmMock).not.toHaveBeenCalled();expect(host.textContent).toContain('已重算');expect(button('确认并保存').disabled).toBe(false)
 confirmMock.mockResolvedValue({trip:trip(),revision:1,issues:[],idempotent:false})
 await click('确认并保存');expect(confirmMock).toHaveBeenCalledTimes(1)
})
it('预览网络失败不丢草案，重试成功前禁止保存',async()=>{
 await chooseAndGenerate([issue()]);apiMock.mockRejectedValueOnce(new Error('network unavailable'))
 await click('缩短');expect(host.textContent).toContain('network unavailable');expect(button('确认并保存').disabled).toBe(true);expect(confirmMock).not.toHaveBeenCalled()
 apiMock.mockResolvedValueOnce({trip:trip(),routes:{},issues:[]});await click('重新核验当前草案');expect(button('确认并保存').disabled).toBe(false)
})
it('最终确认被服务端拒绝时，回填新的修复选项供继续调整',async()=>{
 await chooseAndGenerate();confirmMock.mockRejectedValueOnce(new ApiError(422,'最新路况导致冲突',{issues:[issue('extend_day','最新路况导致冲突')],routes:{}}))
 await click('确认并保存');expect(host.textContent).toContain('最新路况导致冲突');expect(button('当天结束延后')).toBeDefined();expect(button('确认并保存').disabled).toBe(true)
})

it('多方案切换只更新预览，不写旅行；可选标记随请求提交',async()=>{
 await chooseAndGenerate()
 await click('返回调整')
 const optional=host.querySelector<HTMLInputElement>('.optional-choice input')!;optional.click();await nextTick()
 const first=trip(),second=trip();second.days[0]!.activities[0]!.duration=60
 const metric={walkingKm:1,travelMinutes:15,visitMinutes:90,freeMinutes:300,days:[{day:0,walkingKm:1,travelMinutes:15,visitMinutes:90,freeMinutes:300,finish:720,overMinutes:0,waitingMinutes:0}]}
 apiMock.mockResolvedValueOnce({options:[{id:'less-walking',label:'少走路',trip:first,routes:{},issues:[],metrics:metric,includedIds:['quyuan'],omittedIds:[]},{id:'relaxed',label:'轻松休闲',trip:second,routes:{},issues:[],metrics:{...metric,visitMinutes:60,freeMinutes:330},includedIds:['quyuan'],omittedIds:[]}],diagnostic:null,source:'constraint-search',unavailable:[],metricNote:'真实道路',warnings:[],search:{evaluatedStates:4,dayRouteQueries:2,maxDayRouteQueries:12,limited:false,reason:null,routeFailures:[]}})
 await click('生成多方案')
 expect(apiMock).toHaveBeenLastCalledWith('/planner/alternatives','POST',expect.objectContaining({optionalIds:['quyuan']}))
 expect(host.querySelectorAll('.plan-option')).toHaveLength(2);expect(host.querySelector('.plan-option.chosen')?.textContent).toContain('少走路')
 const switcher=host.querySelectorAll<HTMLButtonElement>('.plan-option button')[1]!;switcher.click();await flush()
 expect(host.querySelector('.plan-option.chosen')?.textContent).toContain('轻松休闲')
 expect(host.querySelector('.draft-day')?.textContent).toContain('60 分钟');expect(confirmMock).not.toHaveBeenCalled()
 confirmMock.mockResolvedValue({trip:second,revision:1,issues:[],idempotent:false});await click('确认并保存')
 expect(confirmMock).toHaveBeenCalledWith(second,0)
})

it('搜索没有可核验结果时展示原因，不提供确认保存按钮',async()=>{
 await chooseAndGenerate();await click('返回调整')
 apiMock.mockResolvedValueOnce({options:[],diagnostic:null,source:'constraint-search',unavailable:[{id:'less-walking',reason:'地图预算已满'}],metricNote:'真实道路',warnings:[],search:{evaluatedStates:1,dayRouteQueries:1,maxDayRouteQueries:12,limited:true,reason:'地图预算已满',routeFailures:[]}})
 await click('生成多方案');expect(host.textContent).toContain('地图预算已满');expect(host.textContent).toContain('没有可核验的草案')
 expect([...host.querySelectorAll('button')].some(b=>b.textContent?.includes('确认并保存'))).toBe(false)
 expect(confirmMock).not.toHaveBeenCalled()
})

it('自由描述不需要先填日期即可启动，重复打开不创建新任务',async()=>{
 app.unmount();app=createApp(TripBuilder,{loggedIn:true,hasModel:true});app.mount(host)
 vi.mocked(beginConversation).mockResolvedValue({runId:'conversation-1',status:'running'})
 const text=host.querySelector('textarea')!;text.value='想在杭州轻松逛逛，人还没约齐';text.dispatchEvent(new Event('input'));await nextTick()
 await click('用 AI 对话规划')
 expect(beginConversation).toHaveBeenCalledTimes(1);expect(beginConversation).toHaveBeenCalledWith('想在杭州轻松逛逛，人还没约齐','我的杭州慢游')
 expect(sessionStorage.getItem('tripcanvas.conversation.active.v1')).toContain('conversation-1')
 sessionStorage.removeItem('tripcanvas.conversation.active.v1')
})

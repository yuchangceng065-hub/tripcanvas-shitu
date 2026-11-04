// @vitest-environment happy-dom
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createApp,nextTick,type App} from 'vue'
import TripBuilder from './components/TripBuilder.vue'
import {api,confirmTrip} from './api'
import {ApiError} from './apiError'
import {places,type Trip} from './domain'
vi.mock('./api',()=>({api:vi.fn(),confirmTrip:vi.fn(),connection:{routing:'osrm'}}))
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
beforeEach(()=>{vi.resetAllMocks();host=document.createElement('div');document.body.append(host);app=createApp(TripBuilder,{loggedIn:true,hasModel:false});app.mount(host)})
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

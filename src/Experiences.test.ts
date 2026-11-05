// @vitest-environment happy-dom
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createApp,h,nextTick,type App} from 'vue'
import {api} from './api'
import {createTrip,places} from './domain'
import PlanningLab from './components/PlanningLab.vue'
import GuideImporter from './components/GuideImporter.vue'
import AgentReplay from './components/AgentReplay.vue'
vi.mock('./api',()=>({api:vi.fn(),connection:{map:'osm'}}))
vi.mock('./components/TripMap.vue',()=>({default:{template:'<div>真实地点地图</div>'}}))
const mock=vi.mocked(api);let host:HTMLDivElement,app:App
const flush=async()=>{for(let i=0;i<5;i++){await new Promise(r=>setTimeout(r,0));await nextTick()}}
const button=(s:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent?.includes(s))!
beforeEach(()=>{vi.resetAllMocks();host=document.createElement('div');document.body.append(host)})
afterEach(()=>{app?.unmount();host.remove()})
const evaluation=(t=createTrip())=>({trip:t,routes:{},issues:[],feasible:true,metrics:{walkingKm:2,travelMinutes:30,visitMinutes:180,freeMinutes:100,days:[]},changes:[]})
it('推演新条件使旧核验结果失效，保存分支前无正式行程写入',async()=>{
 const t=createTrip();let resolve:(v:unknown)=>void=()=>{}
 mock.mockImplementation(async(path)=>path.startsWith('/branches?')?[]:path==='/planner/simulate'?new Promise(r=>resolve=r):{})
 app=createApp({render:()=>h(PlanningLab,{trip:t,loggedIn:true})});app.mount(host);await flush()
 resolve({baseline:evaluation(t),repairs:[],runId:'r',note:'有界搜索',search:{evaluated:1,maxEvaluations:8,roadQueries:1}});await flush()
 expect(button('保存为独立情景分支').disabled).toBe(false)
 const slider=host.querySelector<HTMLInputElement>('[aria-label="推演出发时间"]')!;slider.value='660';slider.dispatchEvent(new Event('input'));await nextTick()
 expect(button('保存为独立情景分支')).toBeUndefined()
 expect(host.textContent).toContain('等待道路核验')
 expect(mock.mock.calls.some(c=>c[0]==='/planner/confirm'||c[0]==='/branches')).toBe(false)
})
it('攻略保留出处和否定，合并重复真实地点，批量加入不自动生成',async()=>{
 const place=places[0]!,text='曲院风荷值得去，曲院风荷有空再去，不推荐雷峰塔。';let chosen:unknown
 mock.mockImplementation(async path=>path==='/planner/imports'?[]:{text,runId:'g',mentions:[{key:'1',name:'曲院风荷',quote:'曲院风荷值得去',start:0,end:7,intent:'required',candidates:[place]},{key:'2',name:'曲院风荷',quote:'曲院风荷有空再去',start:8,end:17,intent:'optional',candidates:[place]},{key:'3',name:'雷峰塔',quote:'不推荐雷峰塔',start:18,end:24,intent:'excluded',candidates:[]}]})
 app=createApp({render:()=>h(GuideImporter,{loggedIn:true,onChoose:(p:unknown,o:unknown)=>chosen={p,o}})});app.mount(host);await flush()
 const input=host.querySelector('textarea')!;input.value=text;input.dispatchEvent(new Event('input'));await nextTick();button('提取地点与原文出处').click();await flush()
 const radios=host.querySelectorAll<HTMLInputElement>('input[type=radio]');radios[0]!.checked=true;radios[0]!.dispatchEvent(new Event('change'));radios[2]!.checked=true;radios[2]!.dispatchEvent(new Event('change'));await nextTick()
 expect(host.querySelector('mark')?.textContent).toBe('曲院风荷值得去');expect(host.textContent).toContain('否定提及')
 button('批量加入 1 个').click();expect(chosen).toEqual({p:[place],o:[]})
 expect(mock.mock.calls.some(c=>c[0]==='/planner/confirm')).toBe(false)
})
it('执行回放单步查看真实摘要，播放不触发原任务',async()=>{
 mock.mockImplementation(async path=>path==='/runs'?[{runId:'r',kind:'agent',created:1,status:'completed'}]:{runId:'r',status:'completed',events:[{seq:1,phase:'tool_call',status:'ok',createdAt:1,detail:{tool:'route_day'}},{seq:2,phase:'tool_result',status:'error',createdAt:3,detail:{ok:false,error:'无法按时到达'}}],modelUsage:{providerRequests:2,totalTokens:100,tokensComplete:true}})
 app=createApp({render:()=>h(AgentReplay)});app.mount(host);await flush()
 expect(host.querySelector('pre')?.textContent).toContain('route_day');button('下一步').click();await nextTick()
 expect(host.querySelector('pre')?.textContent).toContain('无法按时到达');expect(host.textContent).toContain('2.00 秒');expect(button('下一步').disabled).toBe(true)
 expect(mock.mock.calls.every(c=>!c[1]||c[1]==='GET')).toBe(true)
})

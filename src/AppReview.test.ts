// @vitest-environment happy-dom
import {afterEach,expect,it,vi} from 'vitest'
import {createApp,nextTick,type App as VueApp} from 'vue'
import {createPinia} from 'pinia'
import App from './App.vue'
import {useTrips} from './store'
import {createTrip,clone} from './domain'
import {api,confirmTrip} from './api'
vi.mock('./api',()=>({api:vi.fn(),confirmTrip:vi.fn(),checkAPI:vi.fn(async()=>({status:'ok',model:false})),connection:{status:'online',model:false,routing:'osrm',map:'osm'},backendConfigured:true,setToken:vi.fn(),remoteSuggestion:vi.fn()}))
vi.mock('./components/TripMap.vue',()=>({default:{render:()=>null}}))
vi.mock('./components/TripBuilder.vue',()=>({default:{render:()=>null}}))
let app:VueApp|undefined,host:HTMLDivElement|undefined
const flush=async()=>{for(let i=0;i<5;i++){await new Promise(r=>setTimeout(r,0));await nextTick()}}
afterEach(()=>{app?.unmount();host?.remove();localStorage.clear();vi.resetAllMocks()})
it('画布手动修改转为草稿，经真实核验入口确认后同步版本号和状态',async()=>{
 localStorage.clear()
 const pinia=createPinia(),store=useTrips(pinia)
 const initial=createTrip('手动编辑验收','2026-11-14',1)
 initial.days[0]!.activities=initial.days[0]!.activities.slice(0,1)
 initial.validationStatus='verified';store.trips=[initial];store.activeId=initial.id
 vi.mocked(api).mockImplementation(async(path)=>{
  if(path==='/auth/me')return {username:'test'}
  if(path==='/planner/preview')return {issues:[],routes:{}}
  return {}
 })
 vi.mocked(confirmTrip).mockImplementation(async(trip,revision)=>({trip:{...trip,revision:revision+1,validationStatus:'verified'},revision:revision+1,issues:[],idempotent:false}))
 host=document.createElement('div');document.body.append(host);app=createApp(App);app.use(pinia).mount(host)
 await flush()
 const changed=clone(initial);changed.days[0]!.activities[0]!.duration=30
 store.commit(changed,'编辑停留',initial.revision);await flush()
 expect(host.textContent).toContain('草稿待核验')
 const button=(text:string)=>[...host!.querySelectorAll('button')].find(b=>b.textContent?.includes(text))!
 button('草稿待核验').click();await flush();expect(confirmTrip).not.toHaveBeenCalled()
 button('确认已核验版本').click();await flush()
 expect(confirmTrip).toHaveBeenCalledWith(expect.objectContaining({revision:2,validationStatus:'draft'}),2)
 expect(store.trips[0]!.revision).toBe(3)
 expect(store.trips[0]!.validationStatus).toBe('verified')
 expect(host.textContent).toContain('已核验 · 查看')
 expect(vi.mocked(api).mock.calls.some(([path,method,body])=>path==='/trips/'+initial.id&&method==='PUT'&&(body as {trip:{revision:number}}).trip.revision===3)).toBe(true)
})

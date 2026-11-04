import { reactive } from 'vue'
import type { Proposal, Trip } from './domain'
import {ApiError,apiErrorMessage} from './apiError'
const deployedAPI=import.meta.env.PROD?'https://tripcanvas-api.onrender.com/api/v1':''
const base=(import.meta.env.VITE_API_BASE || deployedAPI || '/api/v1').replace(/\/$/,'')
export const backendConfigured=!!(import.meta.env.VITE_API_BASE||deployedAPI)
export const connection=reactive({status:'idle' as 'idle'|'checking'|'waking'|'online'|'offline'|'local',attempt:0,model:false,routing:'osrm' as 'osrm'|'amap',map:'osm' as 'osm'|'amap'})
export let apiAvailable=false
interface Health {status:'ok';model:boolean;storage?:string;routing?:'osrm'|'amap';map?:'osm'|'amap'}
let healthPromise:Promise<Health|null>|undefined,lastHealth:Health|null=null,lastChecked=0
const pause=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms))
export function checkAPI(force=false):Promise<Health|null>{
 if(healthPromise)return healthPromise
 if(!force&&Date.now()-lastChecked<30000)return Promise.resolve(lastHealth)
 healthPromise=(async()=>{
  connection.status='checking';connection.attempt=0
  const deadline=Date.now()+(backendConfigured?100000:3000)
  do{
   connection.attempt++
   try{
    const r=await fetch(base+'/health',{cache:'no-store',signal:AbortSignal.timeout(Math.max(1,Math.min(15000,deadline-Date.now())))})
    if(r.ok&&r.headers.get('content-type')?.includes('application/json')){
     const data=await r.json()
     if(data.status==='ok'){apiAvailable=true;connection.status='online';connection.model=!!data.model;connection.routing=data.routing==='amap'?'amap':'osrm';connection.map=data.map==='amap'?'amap':'osm';lastHealth=data;return data}
    }
    // Invalid configuration / denied access should not look like a slow cold start.
    if([400,401,403,404].includes(r.status))break
   }catch{}
   if(!backendConfigured||Date.now()>=deadline)break
   connection.status='waking'
   await pause(Math.min(3000,Math.max(0,deadline-Date.now())))
  }while(Date.now()<deadline)
  apiAvailable=false;lastHealth=null;connection.status=backendConfigured?'offline':'local';return null
 })().finally(()=>{lastChecked=Date.now();healthPromise=undefined})
 return healthPromise
}
let token=''
try{token=sessionStorage.getItem('tripcanvas.session')||''}catch{}
export function setToken(value:string){token=value;try{if(value)sessionStorage.setItem('tripcanvas.session',value);else sessionStorage.removeItem('tripcanvas.session')}catch{}}
export async function api(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 if(backendConfigured&&!await checkAPI())throw new Error('云端服务暂未连接，请点击重新连接；本地行程仍然保留。')
 const headers:Record<string,string>={'Content-Type':'application/json'}
 if(token)headers.Authorization='Bearer '+token
 let r:Response
 const timeout=path==='/planner/alternatives'?180000:path==='/planner/draft'?150000:90000
 try{r=await fetch(base+path,{method,credentials:'include',headers,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout),body:body===undefined?undefined:JSON.stringify(body)})}
 catch(e){if(signal?.aborted)throw e;lastChecked=0;connection.status='offline';apiAvailable=false;throw new Error(method==='GET'?'网络连接失败，请重试':'未能确认云端操作结果，请重新连接后检查；本地行程仍保留。')}
 let d:any;try{d=await r.json()}catch{lastChecked=0;connection.status='offline';apiAvailable=false;throw new Error('云端服务暂不可用，请重新连接后重试')}
 // An HTTP error from a running API (for example model quota exhaustion) is
 // different from a network outage. Keep POI search, auth and routing usable.
 if(!r.ok){if(r.status===401)setToken('');throw new ApiError(r.status,apiErrorMessage(d.detail,r.status),d.detail)}
 return d
}
export async function remoteSuggestion(trip:Trip,day:number,prompt:string):Promise<Proposal>{return api('/planner/suggest','POST',{trip,day,prompt})}
export interface Confirmed {trip:Trip;revision:number;issues:{code:string;message:string}[];idempotent:boolean}
export function confirmTrip(trip:Trip,baseRevision:number):Promise<Confirmed>{return api('/planner/confirm','POST',{trip,baseRevision})}

export interface AgentEvent {seq:number;phase:string;status:string;detail:Record<string,unknown>}
export interface AgentStart {runId:string;status:string}
export function startAgent(body:unknown):Promise<AgentStart>{return api('/planner/agent','POST',body)}
export function parseSSEBlock(block:string):AgentEvent|null{
 const lines=block.split(/\r?\n/);let seq=0,phase='',data=''
 for(const line of lines){
  if(line.startsWith('id:'))seq=Number(line.slice(3).trim())
  else if(line.startsWith('event:'))phase=line.slice(6).trim()
  else if(line.startsWith('data:'))data+=line.slice(5).trim()
 }
 if(!phase)return null
 let detail:Record<string,unknown>={}
 try{detail=JSON.parse(data||'{}')}catch{}
 return {seq,phase,status:String(detail.status??'ok'),detail}
}
export async function streamRun(runId:string,onEvent:(event:AgentEvent)=>void,signal?:AbortSignal,cursor=0):Promise<void>{
 const headers:Record<string,string>={}
 if(token)headers.Authorization='Bearer '+token
 if(cursor)headers['Last-Event-ID']=String(cursor)
 const response=await fetch(base+'/runs/'+runId+'/events',{headers,credentials:'include',signal})
 if(!response.ok||!response.body)throw new Error('无法读取运行轨迹')
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
 while(true){
  const {done,value}=await reader.read();if(done)break
  buffer+=decoder.decode(value,{stream:true})
  let index
  while((index=buffer.indexOf('\n\n'))>=0){
   const event=parseSSEBlock(buffer.slice(0,index));buffer=buffer.slice(index+2)
   if(event)onEvent(event)
  }
 }
 const tail=parseSSEBlock(buffer);if(tail)onEvent(tail)
}

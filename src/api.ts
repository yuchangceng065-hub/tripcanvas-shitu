import { reactive } from 'vue'
import type { Proposal, Trip, Place, Requirements } from './domain'
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
export function setToken(value:string){if(value!==token){try{sessionStorage.removeItem('tripcanvas.conversation.active.v1');sessionStorage.removeItem('tripcanvas.agent.pending.v1')}catch{}}token=value;try{if(value)sessionStorage.setItem('tripcanvas.session',value);else sessionStorage.removeItem('tripcanvas.session')}catch{}}
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
export function confirmTrip(trip:Trip,baseRevision:number,runId?:string):Promise<Confirmed>{return api('/planner/confirm','POST',{trip,baseRevision,...(runId?{runId}:{})})}

export interface AgentEvent {seq:number;phase:string;status:string;detail:Record<string,unknown>}
export interface AgentStart {runId:string;status:string}
export function startAgent(body:unknown,signal?:AbortSignal):Promise<AgentStart>{return api('/planner/agent','POST',body,signal)}
export function parseSSEBlock(block:string):AgentEvent|null{
 const lines=block.split(/\r?\n/);let seq=0,phase='';const data:string[]=[]
 for(const line of lines){
  if(line.startsWith('id:'))seq=Number(line.slice(3).trim())
  else if(line.startsWith('event:'))phase=line.slice(6).trim()
  else if(line.startsWith('data:'))data.push(line.slice(5).trimStart())
 }
 if(!phase)return null
 if(!Number.isSafeInteger(seq)||seq<1)throw new Error('运行事件序号无效，请重新连接')
 const value=JSON.parse(data.join('\n')||'{}')
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('运行事件格式无效')
 // Read the new status/detail envelope and older flat events.
 const detail=value.detail&&typeof value.detail==='object'&&!Array.isArray(value.detail)?value.detail:value
 return {seq,phase,status:String(value.status??'ok'),detail}
}
export async function streamRun(runId:string,onEvent:(event:AgentEvent)=>void,signal?:AbortSignal,cursor=0):Promise<void>{
 const headers:Record<string,string>={}
 if(token)headers.Authorization='Bearer '+token
 if(cursor)headers['Last-Event-ID']=String(cursor)
 const timeout=AbortSignal.timeout(100000)
 const response=await fetch(base+'/runs/'+encodeURIComponent(runId)+'/events',{headers,credentials:'include',signal:signal?AbortSignal.any([signal,timeout]):timeout})
 if(!response.ok||!response.body){
  // This stream uses raw fetch, so it must clear the stale token itself —
  // otherwise the UI keeps showing "logged in" after a 401.
  if(response.status===401)setToken('')
  throw new ApiError(response.status,'无法读取运行轨迹')
 }
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
 try{
  while(true){
   const {done,value}=await reader.read();if(done)break
   buffer+=decoder.decode(value,{stream:true})
   let separator:RegExpMatchArray|null
   while((separator=buffer.match(/\r?\n\r?\n/))){
    const index=separator.index!;const event=parseSSEBlock(buffer.slice(0,index));buffer=buffer.slice(index+separator[0].length)
    if(event)onEvent(event)
   }
  }
  buffer+=decoder.decode()
  const tail=parseSSEBlock(buffer);if(tail)onEvent(tail)
 }finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
}
export interface ConversationView {version:number;status:string;phase:'intake'|'agent';messages:{role:'user'|'assistant';text:string}[];requirements:Omit<Partial<Requirements>,'city'>&{city?:string};mentions:{key:string;text:string;role:'visit'|'origin'|'destination';required:boolean;candidates:Place[];selectedId?:string;error?:string}[];missing:string[];question:string;notes:string[];canConfirm:boolean;remainingSteps:number;remainingReplies:number}
export interface ConversationAnswer {version:number;requestId:string;text?:string;choices?:Record<string,string>;confirm?:boolean;retry?:boolean}
export function beginConversation(text:string,title:string,signal?:AbortSignal):Promise<AgentStart>{return api('/planner/conversations','POST',{text,title},signal)}
export function answerConversation(runId:string,body:ConversationAnswer,signal?:AbortSignal):Promise<AgentStart>{return api('/planner/conversations/'+encodeURIComponent(runId)+'/reply','POST',body,signal)}
export interface AgentRun {conversation?:ConversationView;status:string;events?:AgentEvent[];validation?:unknown;error?:string;question?:string;message?:string}
async function runPause(ms:number,signal?:AbortSignal){
 signal?.throwIfAborted()
 await new Promise<void>((resolve,reject)=>{
  const done=()=>{signal?.removeEventListener('abort',cancel);resolve()}
  const timer=setTimeout(done,ms)
  const cancel=()=>{clearTimeout(timer);signal?.removeEventListener('abort',cancel);reject(signal?.reason??new Error('已停止查看'))}
  signal?.addEventListener('abort',cancel,{once:true})
 })
}
/** Reconnect the same run, replay missed events and poll the authoritative result. */
export async function watchRun(runId:string,onEvent:(event:AgentEvent)=>void,signal?:AbortSignal,cursor=0):Promise<AgentRun>{
 let last=cursor,unavailable=0;const deadline=Date.now()+360000
 const receive=(event:AgentEvent)=>{if(Number.isSafeInteger(event.seq)&&event.seq>last){last=event.seq;onEvent(event)}}
 while(Date.now()<deadline){
  signal?.throwIfAborted()
  try{await streamRun(runId,receive,signal,last)}
  catch(e){
   signal?.throwIfAborted()
   if(e instanceof ApiError&&[401,403,404].includes(e.status))throw e
  }
  try{
   const run:AgentRun=await api('/runs/'+encodeURIComponent(runId),'GET',undefined,signal)
   for(const event of run.events??[])receive(event)
   unavailable=0
   if(run.status!=='running')return run
  }catch(e){
   signal?.throwIfAborted()
   if(e instanceof ApiError&&[401,403,404].includes(e.status))throw e
   if(++unavailable>=3)throw new Error('暂时无法取得运行结果，请继续查看本次运行；无需重新生成。')
  }
  await runPause(Math.min(1000*2**unavailable,5000),signal)
 }
 throw new Error('等待运行结果超时，可继续查看本次运行。')
}

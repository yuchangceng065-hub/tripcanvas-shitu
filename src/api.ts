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
 try{r=await fetch(base+path,{method,credentials:'include',headers,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(path==='/planner/draft'?150000:90000)]):AbortSignal.timeout(path==='/planner/draft'?150000:90000),body:body===undefined?undefined:JSON.stringify(body)})}
 catch(e){if(signal?.aborted)throw e;lastChecked=0;connection.status='offline';apiAvailable=false;throw new Error(method==='GET'?'网络连接失败，请重试':'未能确认云端操作结果，请重新连接后检查；本地行程仍保留。')}
 let d:any;try{d=await r.json()}catch{lastChecked=0;connection.status='offline';apiAvailable=false;throw new Error('云端服务暂不可用，请重新连接后重试')}
 // An HTTP error from a running API (for example model quota exhaustion) is
 // different from a network outage. Keep POI search, auth and routing usable.
 if(!r.ok){if(r.status===401)setToken('');throw new ApiError(r.status,apiErrorMessage(d.detail,r.status))}
 return d
}
export async function remoteSuggestion(trip:Trip,day:number,prompt:string):Promise<Proposal>{return api('/planner/suggest','POST',{trip,day,prompt})}

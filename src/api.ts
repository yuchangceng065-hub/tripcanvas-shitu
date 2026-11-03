import type { Proposal, Trip } from './domain'
const base=(import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/,'')
export let apiAvailable=false
let healthPromise:Promise<any>|undefined
export function checkAPI(){return healthPromise ||= (async()=>{try{const r=await fetch(base+'/health',{signal:AbortSignal.timeout(8000)});if(r.ok){const data=await r.json();apiAvailable=data.status==='ok';return apiAvailable?data:null}}catch{}return null})()}
let token=''
try{token=sessionStorage.getItem('tripcanvas.session')||''}catch{}
export function setToken(value:string){token=value;try{if(value)sessionStorage.setItem('tripcanvas.session',value);else sessionStorage.removeItem('tripcanvas.session')}catch{}}
export async function api(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 const headers:Record<string,string>={'Content-Type':'application/json'}
 if(token)headers.Authorization='Bearer '+token
 const r=await fetch(base+path,{method,credentials:'include',headers,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(60000)]):AbortSignal.timeout(60000),body:body===undefined?undefined:JSON.stringify(body)})
 let d:any;try{d=await r.json()}catch{throw new Error('无法连接后端服务，请稍后重试')}
 if(!r.ok){if(r.status===401)setToken('');throw new Error(typeof d.detail==='string'?d.detail:'请求未能完成')}
 return d
}
export async function remoteSuggestion(trip:Trip,day:number,prompt:string):Promise<Proposal>{return api('/planner/suggest','POST',{trip,day,prompt})}

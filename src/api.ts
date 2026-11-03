import type { Proposal, Trip } from './domain'
const base=import.meta.env.VITE_API_BASE || '/api/v1'
export let apiAvailable=false
export async function checkAPI(){try{const r=await fetch(base+'/health',{signal:AbortSignal.timeout(1500)});if(r.ok){const data=await r.json();apiAvailable=data.status==='ok';return data}}catch{}return null}
export async function api(path:string,method='GET',body?:unknown){const r=await fetch(base+path,{method,credentials:'include',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'请求未能完成');return d}
export async function remoteSuggestion(trip:Trip,day:number,prompt:string):Promise<Proposal>{return api('/planner/suggest','POST',{trip,day,prompt})}

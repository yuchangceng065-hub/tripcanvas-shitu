import {api} from './api'
declare global {interface Window {AMap?:any;_AMapSecurityConfig?:{serviceHost:string}}}
let pending:Promise<any>|undefined
export function loadAmap():Promise<any>{
 if(window.AMap?.Map)return Promise.resolve(window.AMap)
 if(pending)return pending
 pending=(async()=>{
  const config=await api('/map/config')
  if(!config.ready||!config.jsKey||!config.serviceHost)throw new Error('高德地图尚未启用，请稍后重新连接')
  window._AMapSecurityConfig={serviceHost:config.serviceHost}
  return new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.id='tripcanvas-amap-sdk';script.async=true
   const timer=setTimeout(()=>{script.remove();reject(new Error('高德地图加载超时，请重试'))},25000)
   script.onload=()=>{clearTimeout(timer);window.AMap?.Map?resolve(window.AMap):reject(new Error('高德地图初始化失败，请检查网站域名配置'))}
   script.onerror=()=>{clearTimeout(timer);script.remove();reject(new Error('高德地图无法加载，请检查网络后重试'))}
   script.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(config.jsKey)
   document.head.appendChild(script)
  })
 })().catch(e=>{pending=undefined;throw e})
 return pending
}

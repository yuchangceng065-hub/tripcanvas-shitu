<script setup lang="ts">
import {ref,onMounted,onUnmounted,watch} from 'vue'
import {LocateFixed,MapPin,LoaderCircle,RotateCcw} from '@lucide/vue'
import {api} from '../api'
import {loadAmap} from '../amapLoader'
import type {Entry} from '../domain'
import type {RouteState} from '../routes'
const props=defineProps<{entries:Entry[];selected:string;state:RouteState;mode:'walk'|'drive'}>()
const emit=defineEmits<{select:[id:string];retry:[]}>()
const container=ref<HTMLDivElement>(),mapError=ref(''),loading=ref(true)
let A:any,map:any,overlays:any[]=[],markers=new Map<string,any>(),generation=0,disposed=false,observer:ResizeObserver|undefined,lastFit=''
const identity=()=>props.entries.map(e=>e.activity.id+':'+e.place.id).join(',')+props.mode+':'+props.state.route?.fetchedAt
function fit(){if(map&&overlays.length)map.setFitView(overlays,false,[80,40,135,40],15)}
function markerContent(label:string,active:boolean,parking=false){const div=document.createElement('div');div.className='amap-road-pin'+(active?' active':'')+(parking?' parking':'');div.textContent=label;return div}
async function render(){
 if(!map)return
 const version=++generation,entries=[...props.entries],route=props.state.status==='ready'&&props.state.route?.source==='amap'?props.state.route:undefined
 try{
  const coords=route?.locations||(await api('/map/coordinates','POST',{placeIds:entries.map(e=>e.place.id)})).locations
  if(version!==generation||disposed)return
  if(overlays.length)map.remove(overlays);overlays=[];markers.clear()
  entries.forEach((e,i)=>{
   const position=coords[e.place.id];if(!position)return
   const marker=new A.Marker({position,content:markerContent(String(i+1),e.activity.id===props.selected),offset:new A.Pixel(-17,-17),title:e.place.name,zIndex:100+i})
   marker.on('click',()=>emit('select',e.activity.id));markers.set(e.activity.id,marker);overlays.push(marker)
  })
  if(route){
   for(const leg of route.legs)for(const segment of leg.segments||[]){
    if(!segment.geometry)continue
    const line=new A.Polyline({path:segment.geometry.coordinates,strokeColor:segment.mode==='walk'?'#7691ad':'#2d7659',strokeWeight:segment.mode==='walk'?4:5,strokeStyle:segment.mode==='walk'?'dashed':'solid',strokeDasharray:[7,5],showDir:segment.mode==='drive',lineJoin:'round',zIndex:30})
    overlays.push(line)
   }
   const seen=new Set<string>()
   for(const access of route.accessPoints||[]){
    const p=access.parking;if(seen.has(p.id))continue;seen.add(p.id)
    const marker=new A.Marker({position:p.entrance||[p.lng,p.lat],content:markerContent('P',false,true),offset:new A.Pixel(-12,-12),title:p.name+(access.confirmed?'（已选择）':'（待确认）'),zIndex:80})
    overlays.push(marker)
   }
  }
  map.add(overlays);mapError.value=''
  if(lastFit!==identity()){fit();lastFit=identity()}
 }catch(e){if(version===generation&&!disposed)mapError.value=(e as Error).message}
}
async function init(){
 loading.value=true;mapError.value=''
 try{A=await loadAmap();if(disposed||!container.value)return;map=new A.Map(container.value,{center:[120.15,30.25],zoom:13,viewMode:'2D',resizeEnable:true});await render();observer=new ResizeObserver(()=>{map?.resize?.();fit()});observer.observe(container.value)}catch(e){mapError.value=(e as Error).message}finally{loading.value=false}
}
function retry(){if(map)void render();else void init();emit('retry')}
onMounted(init)
watch(()=>[props.entries.map(e=>e.activity.id+':'+e.place.id).join(','),props.mode,props.state],()=>void render())
watch(()=>props.selected,id=>{props.entries.forEach((e,i)=>markers.get(e.activity.id)?.setContent(markerContent(String(i+1),e.activity.id===id)))})
onUnmounted(()=>{disposed=true;generation++;observer?.disconnect();map?.destroy();map=undefined})
</script>
<template>
 <div class="trip-map real-map amap-trip-map">
  <div ref="container" class="leaflet-canvas" aria-label="高德行程地图，区分步行、驾车与停车接驳点"/>
  <div class="map-top"><span class="map-label"><MapPin :size="14"/>杭州</span><span class="map-live"><i/>高德地图</span></div>
  <div class="road-status" :class="{failed:!!mapError||state.status==='error'}" role="status">
   <template v-if="loading"><LoaderCircle :size="14" class="spin"/>正在加载高德地图…</template>
   <template v-else-if="mapError">{{mapError}}<button @click="retry"><RotateCcw :size="12"/>重试</button></template>
   <template v-else-if="state.status==='loading'"><LoaderCircle :size="14" class="spin"/>正在核验步行与道路接驳…</template>
   <template v-else-if="state.status==='ready'"><span class="road-swatch"/>{{mode==='walk'?'步行路线':'自驾＋步行接驳'}}<small v-if="mode==='drive'&&!state.route?.accessConfirmed"> · 停车点待确认</small></template>
   <template v-else-if="state.status==='error'">{{state.error}}<button @click="emit('retry')">重试路线</button></template>
   <span v-else>添加至少两个地点，开始规划路线</span>
  </div>
  <div class="transport-legend"><span><i class="drive"/>驾车</span><span><i class="walk"/>步行</span><span>P 停车接驳</span></div>
  <button class="fit-map" aria-label="查看完整路线" @click="fit"><LocateFixed :size="18"/></button>
 </div>
</template>

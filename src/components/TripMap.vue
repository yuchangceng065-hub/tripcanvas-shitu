<script setup lang="ts">
import { onMounted,onUnmounted,ref,watch } from 'vue'
import * as L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { LocateFixed,MapPin,LoaderCircle,RotateCcw } from '@lucide/vue'
import type { Entry } from '../domain'
import type { RouteState } from '../routes'
const props=defineProps<{entries:Entry[];selected:string;state:RouteState;mode:'walk'|'drive'}>()
const emit=defineEmits<{select:[id:string];retry:[]}>()
const container=ref<HTMLDivElement>(),tileError=ref(false)
let map:L.Map|undefined,markers:L.LayerGroup,roads:L.LayerGroup,tiles:L.TileLayer,observer:ResizeObserver
let fittedKey='',drawableKey=''
const markerById=new Map<string,L.Marker>()
const key=()=>props.entries.map(e=>e.activity.id+':'+e.place.id).join(',')+props.mode
function fit(){if(!map||!container.value?.clientHeight||!container.value.clientWidth)return;const coordinates=props.state.route?.geometry.coordinates.map(p=>[p[1],p[0]] as L.LatLngTuple)||props.entries.map(e=>[e.place.lat,e.place.lng] as L.LatLngTuple);if(coordinates.length)map.fitBounds(L.latLngBounds(coordinates),{paddingTopLeft:[45,100],paddingBottomRight:[45,150],maxZoom:15,animate:false});fittedKey=key()}
function icon(i:number,active:boolean){return L.divIcon({className:'road-pin'+(active?' active':''),html:`<span>${i+1}</span>`,iconSize:[34,34],iconAnchor:[17,17]})}
function render(){if(!map)return;markers.clearLayers();markerById.clear();props.entries.forEach((e,i)=>{const marker=L.marker([e.place.lat,e.place.lng],{icon:icon(i,e.activity.id===props.selected),title:`选择${e.place.name}`,alt:e.place.name,keyboard:true});const text=document.createElement('span');text.textContent=e.place.name;marker.bindTooltip(text,{direction:'top',offset:[0,-17]});marker.on('click',()=>emit('select',e.activity.id));marker.addTo(markers);markerById.set(e.activity.id,marker)});drawRoads();if(fittedKey!==key())fit()}
function drawRoads(){if(!map)return;roads.clearLayers();const route=props.state.status==='ready'?props.state.route:undefined;if(!route){drawableKey='';return}const points=route.geometry.coordinates.map(p=>[p[1],p[0]] as L.LatLngTuple);L.polyline(points,{color:'#fff',weight:8,opacity:.9,interactive:false}).addTo(roads);L.polyline(points,{color:'#2d7659',weight:4,opacity:1,interactive:false}).addTo(roads);route.waypoints.forEach((w,i)=>{const e=props.entries[i];if(e&&w.distance>30)L.polyline([[e.place.lat,e.place.lng],[w.location[1],w.location[0]]],{color:'#7c806d',weight:2,dashArray:'4 5',interactive:false}).addTo(roads)});if(drawableKey!==key())fit();drawableKey=key()}
function retryTiles(){tileError.value=false;tiles?.redraw()}
onMounted(()=>{if(!container.value)return;map=L.map(container.value,{center:[30.245,120.145],zoom:14,zoomControl:false,scrollWheelZoom:true});L.control.zoom({position:'topright',zoomInTitle:'放大地图',zoomOutTitle:'缩小地图'}).addTo(map);tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,updateWhenIdle:true,keepBuffer:1,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors · <a href="https://routing.openstreetmap.de/about.html" target="_blank" rel="noopener">FOSSGIS / OSRM</a> · <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noopener">纠正地图</a>'}).addTo(map);tiles.on('tileerror',()=>tileError.value=true);markers=L.layerGroup().addTo(map);roads=L.layerGroup().addTo(map);render();observer=new ResizeObserver(()=>{if(container.value?.clientHeight){map?.invalidateSize({pan:false});fit()}});observer.observe(container.value)})
watch(()=>props.entries.map(e=>e.activity.id+':'+e.place.id).join(',')+props.mode,render)
watch(()=>props.state,drawRoads)
watch(()=>props.selected,id=>{props.entries.forEach((e,i)=>markerById.get(e.activity.id)?.setIcon(icon(i,e.activity.id===id)));const marker=markerById.get(id);if(marker&&map&&container.value?.clientHeight){map.panInside(marker.getLatLng(),{paddingTopLeft:[35,90],paddingBottomRight:[45,145]});marker.openTooltip()}})
onUnmounted(()=>{observer?.disconnect();map?.remove();map=undefined})
</script>
<template>
 <div class="trip-map real-map">
  <div ref="container" class="leaflet-canvas" aria-label="OpenStreetMap 行程地图，可缩放、拖动并选择地点"/>
  <div class="map-top"><span class="map-label"><MapPin :size="14"/>杭州 · 西湖</span><span class="map-live"><i/>OpenStreetMap</span></div>
  <div class="road-status" :class="{failed:state.status==='error'}" role="status">
   <template v-if="state.status==='loading'"><LoaderCircle :size="14" class="spin"/><span>正在规划{{mode==='walk'?'步行':'驾车'}}道路路线…</span></template>
   <template v-else-if="state.status==='ready'"><span class="road-swatch"/><span>{{mode==='walk'?'步行':'驾车'}}道路路线 · {{state.route?.cached?'已缓存':'已更新'}}</span></template>
   <template v-else-if="state.status==='error'"><span>{{state.error||'路线暂不可用'}}</span><button @click="emit('retry')"><RotateCcw :size="12"/>重试</button></template>
   <span v-else>添加至少两个地点，开始规划路线</span>
  </div>
  <div v-if="tileError" class="tile-error" role="status">部分地图未加载<button @click="retryTiles">重试地图</button></div>
  <button class="fit-map" aria-label="查看完整路线" title="查看完整路线" @click="fit"><LocateFixed :size="18"/></button>
 </div>
</template>

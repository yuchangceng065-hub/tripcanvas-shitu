<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {LoaderCircle,ParkingCircle,Check,Car} from '@lucide/vue'
import {api} from '../api'
import {placeById,registerPlaces,type Trip,type Place,type Mobility} from '../domain'
import type {RoadRoute} from '../routes'
const props=defineProps<{trip:Trip;day:number;route?:RoadRoute}>()
const emit=defineEmits<{apply:[settings:Mobility]}>()
const ids=[...new Set(props.trip.days[props.day]!.activities.map(a=>a.placeId))]
const choices=ref<Record<string,Place[]>>({}),selected=ref<Record<string,string>>({...props.trip.mobility?.parkingByPlace}),buffer=ref(props.trip.mobility?.parkingMinutes??5)
const busy=ref(false),error=ref(''),errors=ref<Record<string,string>>({}),controller=new AbortController()
async function load(){
 if(busy.value)return
 busy.value=true;error.value=''
 for(const id of ids){
  if(controller.signal.aborted)break
  try{
   const data=await api('/places/'+encodeURIComponent(id)+'/parking','GET',undefined,controller.signal);registerPlaces(data.places)
   let available:Place[]=data.places
   const saved=selected.value[id]&&placeById(selected.value[id]!)
   if(saved&&!available.some(p=>p.id===saved.id))available=[saved,...available]
   choices.value[id]=available
   const recommended=props.route?.accessPoints?.find(a=>a.placeId===id)?.parking.id
   if(!selected.value[id])selected.value[id]=available.find(p=>p.id===recommended)?.id||available.find(p=>!p.restrictedAccess)?.id||''
   errors.value[id]=''
  }catch(e){if(!controller.signal.aborted)errors.value[id]=(e as Error).message}
 }
 busy.value=false
}
function submit(){
 if(busy.value)return
 if(ids.some(id=>!selected.value[id]||!choices.value[id]?.some(p=>p.id===selected.value[id]))){error.value='请为当天每一站选择停车点；没有候选时，可重试或切换步行。';return}
 emit('apply',{parkingByPlace:{...props.trip.mobility?.parkingByPlace,...Object.fromEntries(ids.map(id=>[id,selected.value[id]!]))},parkingMinutes:Number(buffer.value)})
}
onMounted(load);onUnmounted(()=>controller.abort())
</script>
<template>
 <div class="mobility-settings"><span class="eyebrow">A WALK, A DRIVE, A BETTER JOURNEY</span><h2>交通与停车</h2>
 <p class="modal-description">Day {{day+1}} · 自驾＋步行。按车辆初始停在首站所选停车点计算；每站游览后回到车辆所在位置，再继续开车。</p>
 <p class="form-hint">同一停车点覆盖连续两站时，直接步行游览，车辆留在原处。候选来自高德附近搜索，不代表有空余车位或保证开放。</p>
 <div v-if="busy" class="mobility-loading" role="status"><LoaderCircle :size="16" class="spin"/>正在查询附近停车接驳点…</div>
 <section v-for="id in ids" :key="id" class="parking-choice"><h3><ParkingCircle :size="17"/>{{placeById(id).name}}</h3><label>停车接驳点<select v-model="selected[id]" :disabled="busy||!choices[id]?.length"><option value="">{{choices[id]?.length?'请选择':'尚无可用候选'}}</option><option v-for="p in choices[id]" :key="p.id" :value="p.id">{{p.name}}{{p.restrictedAccess?'（需确认通行资格）':''}}</option></select></label><p v-if="selected[id]&&placeById(selected[id]!)" class="form-hint">{{placeById(selected[id]!).address}}</p><p v-if="selected[id]&&placeById(selected[id]!)?.restrictedAccess" class="form-error">该停车点可能限制外来车辆，请先确认通行资格，或改选校外、公共停车点。</p><p v-if="errors[id]" class="form-error">{{errors[id]}}</p></section>
 <label>每次驾车的取车与停车预留<select v-model.number="buffer" :disabled="busy"><option v-for="n in [0,5,10,15,20,30]" :key="n" :value="n">{{n}} 分钟</option></select></label><p class="form-hint">这是你设置的预留时间，会计入行程；不是实时停车或排队预测。景区内部路线与入口开放仍需确认。</p>
 <p v-if="error" class="form-error" role="alert">{{error}}</p><div class="modal-actions"><button class="button quiet" @click="load" :disabled="busy">重新查询</button><button class="button primary" :disabled="busy||ids.some(id=>!selected[id]||errors[id])" @click="submit"><Check :size="16"/>确认停车点，预览路线</button></div>
 </div>
</template>

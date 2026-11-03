<script setup lang="ts">
import {ref,computed} from 'vue'
import {Search,Plus,Check,MapPin,LoaderCircle,ExternalLink} from '@lucide/vue'
import {places,registerPlaces,type Place} from '../domain'
import {api} from '../api'
const props=defineProps<{selected?:string[];label?:string}>()
const emit=defineEmits<{choose:[place:Place]}>()
const query=ref(''),results=ref<Place[]>([]),busy=ref(false),error=ref(''),searched=ref(false)
const local=computed(()=>places.filter(p=>!p.id.startsWith('osm:')&&(!query.value||p.name.includes(query.value)||p.kind.includes(query.value))))
let sequence=0
async function search(){if(busy.value||query.value.trim().length<2)return;const version=++sequence;const q=query.value.trim();busy.value=true;error.value='';searched.value=true;results.value=[];try{const data=await api('/places/search?q='+encodeURIComponent(q));if(version!==sequence||q!==query.value.trim())return;registerPlaces(data.places);results.value=data.places}catch(e){if(version===sequence)error.value=(e as Error).message}finally{if(version===sequence)busy.value=false}}
function change(){sequence++;busy.value=false;searched.value=false;results.value=[];error.value=''}
</script>
<template>
 <div class="place-picker">
  <form class="poi-search" @submit.prevent="search"><Search :size="17"/><input v-model="query" @input="change" placeholder="搜索杭州地点，例如灵隐寺、咖啡店" aria-label="真实地点名称" maxlength="100"/><button class="button primary small" :disabled="busy||query.trim().length<2"><LoaderCircle v-if="busy" class="spin" :size="15"/>{{busy?'搜索中':'搜索'}}</button></form>
  <p class="form-hint">点击搜索后查询真实地点；请按地址和地图区分同名结果。</p>
  <p v-if="error" class="form-error" role="alert">{{error}}</p>
  <div v-if="searched" class="poi-results"><h3>外部搜索结果</h3><article v-for="p in results" :key="p.id"><MapPin :size="18"/><div><strong>{{p.name}}</strong><p>{{p.address}}</p><small>OpenStreetMap · WGS84 · 开放与费用待确认</small><a :href="p.coordinateSource" target="_blank" rel="noopener noreferrer">核对地图位置<ExternalLink :size="11"/></a></div><button type="button" class="circle-add" :aria-label="`选择${p.name}`" :disabled="selected?.includes(p.id)" @click="emit('choose',p)"><Check v-if="selected?.includes(p.id)" :size="17"/><Plus v-else :size="17"/></button></article><p v-if="!busy&&!error&&!results.length" class="form-hint">没有匹配地点，请换用具体名称或附近街道。</p></div>
  <div class="poi-results"><h3>杭州精选地点</h3><article v-for="p in local" :key="p.id"><MapPin :size="18"/><div><strong>{{p.name}}</strong><p>{{p.kind}} · {{p.tag}}</p><small>已核对 OSM 坐标 · 营业信息待确认</small></div><button type="button" class="circle-add" :disabled="selected?.includes(p.id)" :aria-label="`选择${p.name}`" @click="emit('choose',p)"><Check v-if="selected?.includes(p.id)" :size="17"/><Plus v-else :size="17"/></button></article></div>
  <p class="poi-attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a> · 搜索由 <a href="https://photon.komoot.io" target="_blank" rel="noopener noreferrer">Photon</a> 提供；公开服务无可用性保证。</p>
 </div>
</template>

<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {api} from '../api'
import TripMap from './TripMap.vue'
import {registerPlaces,type Entry,type Place} from '../domain'
const props=defineProps<{loggedIn:boolean}>(),emit=defineEmits<{choose:[places:Place[],optionalIds:string[]];login:[]}>()
interface Mention {key:string;name:string;quote:string;start:number;end:number;intent:'required'|'optional'|'excluded';candidates:Place[];error?:string}
interface Import {text:string;mentions:Mention[];runId:string}
const text=ref(''),result=ref<Import|null>(null),chosen=ref<Record<string,string>>({}),active=ref<Mention|null>(null),error=ref(''),busy=ref(false),recent=ref<{runId:string;created:number;characters:number}[]>([]),restored=ref('')
let generation=0
onUnmounted(()=>generation++)
const selected=computed(()=>{const unique=new Map<string,{place:Place;optional:boolean}>();for(const m of result.value?.mentions??[]){const place=m.candidates.find(p=>p.id===chosen.value[m.key]);if(place){const previous=unique.get(place.id);unique.set(place.id,{place,optional:m.intent==='optional'&&(previous?.optional??true)})}}return [...unique.values()]})
const mapEntries=computed<Entry[]>(()=>{const visible=selected.value.map(x=>x.place);for(const p of active.value?.candidates??[])if(!visible.some(x=>x.id===p.id))visible.push(p);return visible.map(p=>({place:p,activity:{id:p.id,placeId:p.id,duration:p.duration,lock:'none'},start:0,end:p.duration,earliest:0,travel:0,distance:0,conflict:false}))})
const mapPin=ref('')
function selectPin(id:string){mapPin.value=id;const hit=result.value?.mentions.find(m=>m.candidates.some(p=>p.id===id));if(hit)active.value=hit}
const before=computed(()=>result.value?.text.slice(0,active.value?.start??0)||'')
const after=computed(()=>result.value?.text.slice(active.value?.end??0)||'')
function setResult(value:Import){result.value=value;chosen.value={};active.value=value.mentions[0]??null;registerPlaces(value.mentions.flatMap(m=>m.candidates))}
async function loadRecent(){const ticket=generation;if(props.loggedIn)try{const data=await api('/planner/imports');if(ticket===generation)recent.value=data}catch{}}
onMounted(loadRecent)
watch(()=>props.loggedIn,value=>{generation++;if(value)void loadRecent();else{result.value=null;recent.value=[];text.value='';chosen.value={};active.value=null}})
async function extract(){if(!props.loggedIn){emit('login');return}const ticket=++generation;busy.value=true;error.value='';result.value=null
 try{const data=await api('/planner/import-guide','POST',{text:text.value});if(ticket!==generation)return;setResult(data);await loadRecent()}catch(e){if(ticket===generation)error.value=(e as Error).message}finally{if(ticket===generation)busy.value=false}}
async function restore(){if(!restored.value)return;const ticket=++generation;busy.value=true;error.value='';try{const run=await api('/runs/'+restored.value);if(ticket!==generation)return;setResult({...run.import,runId:restored.value});text.value=run.import.text}catch(e){if(ticket===generation)error.value=(e as Error).message}finally{if(ticket===generation)busy.value=false}}
function confirm(){emit('choose',selected.value.map(x=>x.place),selected.value.filter(x=>x.optional).map(x=>x.place.id))}
</script>
<template>
 <section class="guide-importer" aria-label="攻略文字导入">
  <div class="feature-heading"><span class="eyebrow">FROM WORDS TO PLACES</span><h3>把收藏的攻略，放到地图上</h3><p class="form-hint">粘贴杭州攻略，提取最多 8 个地点；保留原文出处，确认地址后批量加入。攻略中的日期和宣传信息不会自动成为出行条件。</p></div>
  <label v-if="recent.length">继续之前的导入<select v-model="restored" @change="restore" :disabled="busy"><option value="">选择导入记录</option><option v-for="r in recent" :key="r.runId" :value="r.runId">{{new Date(r.created*1000).toLocaleString('zh-CN')}} · {{r.characters}} 字</option></select></label>
  <label>攻略原文<textarea v-model="text" rows="5" minlength="10" maxlength="6000" :disabled="busy" placeholder="例如：曲院风荷适合慢慢散步，浙江省博物馆孤山馆区值得一看，丝绸博物馆有空再去。"/></label>
  <button type="button" class="button quiet" @click="extract" :disabled="busy||text.trim().length<10">{{busy?'正在提取并查询真实地点…':'提取地点与原文出处'}}</button><p v-if="error" class="form-error" role="alert">{{error}}</p>
  <div v-if="result" class="guide-result"><div class="guide-map" v-if="mapEntries.length"><TripMap :entries="mapEntries" :selected="mapPin" :state="{status:'empty'}" mode="walk" @select="selectPin"/><span>仅展示候选位置，尚未规划道路与时间</span></div><article class="source-text"><strong>原文核对</strong><p v-if="active">{{before}}<mark>{{result.text.slice(active.start,active.end)}}</mark>{{after}}</p><p v-else>{{result.text}}</p></article>
   <div><fieldset v-for="mention in result.mentions" :key="mention.key" class="guide-mention" :class="{focused:active?.key===mention.key}" @focusin="active=mention" @click="active=mention"><legend>{{mention.name}} · {{({required:'建议去',optional:'可选',excluded:'原文不推荐'} as const)[mention.intent]}}</legend><button class="text-button" type="button" @click="active=mention">定位原文：{{mention.quote}}</button>
    <p v-if="mention.intent==='excluded'" class="form-hint">否定提及，未搜索也不会加入候选。</p>
    <template v-else><label v-for="place in mention.candidates" :key="place.id"><input type="radio" :name="'guide-'+mention.key" :value="place.id" v-model="chosen[mention.key]"/><span>{{place.name}}<small>{{place.address}} · {{place.provider==='amap'?'高德':'地点搜索'}}</small></span></label><label><input type="radio" :name="'guide-'+mention.key" value="" v-model="chosen[mention.key]"/>暂不加入</label><p v-if="!mention.candidates.length" class="form-hint">{{mention.error||'未找到结果，可在地点搜索中换用具体名称。'}}</p></template>
   </fieldset></div>
   <p v-if="!result.mentions.length" class="form-hint">没有提取到可核对的地点，请补充包含具体地点名称的原文。</p>
   <button class="button primary full" @click="confirm" :disabled="busy||!selected.length">批量加入 {{selected.length}} 个已确认地点</button><p class="form-hint">同一地点自动合并；原文中的“有空再去”保留为可选。提取结果请核对原文，尚未生成或保存正式行程。</p>
  </div>
 </section>
</template>

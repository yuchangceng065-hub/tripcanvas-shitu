<script setup lang="ts">
import type {PlanComparison} from '../planningOptions'
import {placeById} from '../domain'
import type {PlanOption} from '../planningOptions'
defineProps<{comparison:PlanComparison;selected:string;busy?:boolean}>()
defineEmits<{choose:[id:string]}>()
function name(id:string){return placeById(id)?.name??id}
function minutes(value:number){return `${Math.floor(value/60)} 小时 ${value%60} 分`}
function queriedAt(option:PlanOption){const dates=Object.values(option.routes).map(r=>r.fetchedAt).filter(Number.isFinite);return dates.length?new Date(Math.min(...dates)).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):''}
function parkingPending(option:PlanOption){return option.trip.mode==='drive'&&Object.values(option.routes).some(r=>r.source==='amap'&&!r.accessConfirmed)}
</script>
<template>
 <section class="plan-comparison" aria-label="真实路线方案比较">
  <h3>同一份需求，几种旅行方式。</h3>
  <p class="form-hint">{{comparison.metricNote}}</p>
  <div class="option-grid">
   <article v-for="(option,index) in comparison.options" :key="option.id" class="plan-option" :class="{chosen:selected===option.id}">
    <div class="option-heading"><strong>{{option.label}}</strong><span>道路与硬约束已核验</span></div>
    <dl class="option-metrics">
     <div><dt>转场步行</dt><dd>{{option.metrics.walkingKm===null?'未能拆分':option.metrics.walkingKm.toFixed(1)+' km'}}</dd></div>
     <div><dt>交通与接驳</dt><dd>{{minutes(option.metrics.travelMinutes)}}</dd></div>
     <div><dt>游览停留</dt><dd>{{minutes(option.metrics.visitMinutes)}}</dd></div>
     <div><dt>可支配空余</dt><dd>{{minutes(option.metrics.freeMinutes)}}</dd></div>
    </dl>
    <div class="option-days"><p v-for="(day,di) in option.trip.days" :key="day.date"><strong>Day {{di+1}}</strong> {{day.activities.map(a=>name(a.placeId)).join(' → ')||'自由活动'}}<small>空余 {{option.metrics.days[di]?.freeMinutes??0}} 分钟</small></p></div>
    <p class="option-tradeoff"><strong>{{option.includedIds.length}} 个游览地点</strong><span v-if="option.omittedIds.length"> · 舍弃可选：{{option.omittedIds.map(name).join('、')}}</span><span v-else> · 保留全部候选</span></p>
    <p v-if="queriedAt(option)" class="form-hint">道路查询于 {{queriedAt(option)}}，非未来出行日路况预测。</p>
    <p v-if="parkingPending(option)" class="form-hint">按首站停车点已有车辆计算；推荐停车点待确认。</p>
    <p v-if="index&&comparison.options[0]?.metrics.walkingKm===option.metrics.walkingKm" class="form-hint">步行与第一套相同，差异请查看分日、停留和空余。</p>
    <button class="button full" :class="selected===option.id?'primary':'quiet'" @click="$emit('choose',option.id)" :aria-pressed="selected===option.id" :disabled="busy">{{selected===option.id?'正在预览此方案':'预览这套方案'}}</button>
   </article>
  </div>
  <p v-if="!comparison.options.length" class="form-error">本轮未找到满足全部硬约束的方案。下方展示最接近的草案及冲突，不能直接保存。</p>
  <p v-for="missing in comparison.unavailable" :key="missing.id" class="form-hint">{{({'less-walking':'少走路',immersive:'充分游览',relaxed:'轻松休闲'} as Record<string,string>)[missing.id]}}：{{missing.reason}}</p>
  <p class="search-evidence">已检查 {{comparison.search.evaluatedStates}} 个候选 · 查询 {{comparison.search.dayRouteQueries}} 条整日路线（上限 {{comparison.search.maxDayRouteQueries}}）<span v-if="comparison.search.limited"> · {{comparison.search.reason}}</span></p>
 </section>
</template>

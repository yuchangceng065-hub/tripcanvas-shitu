<script setup lang="ts">
import { computed,ref } from 'vue'
import { Plus,Minus,LocateFixed,Layers,Compass,MapPin,ArrowUpRight } from '@lucide/vue'
import type { Entry } from '../domain'
const props=defineProps<{entries:Entry[];selected:string;showAll?:boolean}>()
const emit=defineEmits<{select:[id:string]}>()
const zoom=ref(1),offset=ref({x:0,y:0}),drag=ref<{x:number;y:number;ox:number;oy:number}|null>(null),terrain=ref(false)
const points=computed(()=>props.entries.map(e=>`${e.place.x},${e.place.y}`).join(' '))
function down(e:PointerEvent){if((e.target as Element).closest('.pin'))return;drag.value={x:e.clientX,y:e.clientY,ox:offset.value.x,oy:offset.value.y};(e.currentTarget as Element).setPointerCapture(e.pointerId)}
function move(e:PointerEvent){if(drag.value)offset.value={x:drag.value.ox+(e.clientX-drag.value.x),y:drag.value.oy+(e.clientY-drag.value.y)}}
const labelOffsets:Record<string,[number,number,number]>={quyuan:[-25,-35,-130],museum:[0,-57,-65],louwailou:[-28,35,-130],baidi:[28,-20,0],hubin:[25,0,0]}
function label(id:string,index:number){return labelOffsets[id]||[index%2?24:-24,-34,index%2?0:-130]}
const blocks=Array.from({length:60},(_,i)=>({x:690+(i%5)*45,y:160+Math.floor(i/5)*44,w:30+(i%3)*3,h:26+(i%2)*5}))
</script>
<template>
 <div class="trip-map" :class="{terrain}">
  <div class="map-top"><span class="map-label"><MapPin :size="14"/> 杭州 · 西湖</span><span class="map-live"><i/>路线示意</span></div>
  <svg class="map-svg" viewBox="0 0 900 760" role="group" aria-label="西湖区域行程示意图，可点击地点并拖动画布" @pointerdown="down" @pointermove="move" @pointerup="drag=null" @pointercancel="drag=null">
   <defs><pattern id="map-grain" width="6" height="6" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r=".5" fill="#829482" opacity=".16"/></pattern><filter id="pinshadow" x="-100%" y="-100%" width="300%" height="300%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-opacity=".13"/></filter></defs>
   <rect width="900" height="760" fill="#eeeee4"/>
   <g :transform="`translate(${offset.x} ${offset.y}) translate(450 380) scale(${zoom}) translate(-450 -380)`">
    <path d="M0 0H640L628 68 567 92 474 77 415 104 320 110 281 150 217 166 216 228 165 299 141 433 184 490 227 549 259 612 385 649 394 760H0Z" fill="#dce5ce"/>
    <path d="M0 78Q140 7 221 61T398 46M0 136Q118 73 192 111T331 86M0 194Q94 135 151 166T290 139M0 490Q77 452 122 489T231 542M0 550Q102 518 150 568T287 622M0 609Q87 582 156 627T335 691" fill="none" stroke="#ccd9be" stroke-width="1.6"/>
    <path d="M255 202C265 169 297 157 326 162L408 175 461 155 503 155 563 201C590 227 621 268 628 321L630 436C627 495 592 542 540 561L455 584 344 564C286 551 258 520 250 471L225 364C216 314 223 252 255 202Z" fill="#b9d5d2"/>
    <path d="M265 212C280 185 305 183 334 186L409 199 472 178 498 177 552 218C578 251 606 277 605 326L607 436C603 484 578 520 531 539L452 562 346 540C300 532 280 506 270 465L247 359C239 309 244 253 265 212Z" fill="none" stroke="#afcecc" stroke-width="1"/>
    <path d="M272 187C242 312 294 435 307 548" stroke="#e5e8d4" stroke-width="12" fill="none"/>
    <path d="M272 187C242 312 294 435 307 548" stroke="#cdd8bc" stroke-width="6" fill="none"/>
    <path d="M345 196C410 212 466 186 532 169" stroke="#e8e9d5" stroke-width="11" fill="none"/>
    <path d="M345 196C410 212 466 186 532 169" stroke="#cdd8bc" stroke-width="5" fill="none"/>
    <ellipse cx="369" cy="190" rx="62" ry="28" fill="#d4dfc4" transform="rotate(-8 369 190)"/>
    <path d="M428 412Q447 387 466 409T460 445L435 442Z" fill="#d8e0c6"/><path d="M338 379q15-18 30 2t-5 29l-22-7Z" fill="#d8e0c6"/>
    <g fill="#e1e2d7" stroke="#d7d9cd" stroke-width=".7"><rect v-for="(b,i) in blocks" :key="i" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="3"/></g>
    <g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M0 130L185 145 277 139 367 134 440 110 530 105 620 140 900 117M184 0L201 126 194 200 168 283 161 420 202 523 295 593 420 639 560 650 650 620 900 630M638 0L647 148 678 277 673 443 650 620 677 760M770 0L761 760M663 294L900 294M673 426L900 426M656 555L900 555" stroke="#d7d9ce" stroke-width="13"/><path d="M0 130L185 145 277 139 367 134 440 110 530 105 620 140 900 117M184 0L201 126 194 200 168 283 161 420 202 523 295 593 420 639 560 650 650 620 900 630M638 0L647 148 678 277 673 443 650 620 677 760M770 0L761 760M663 294L900 294M673 426L900 426M656 555L900 555" stroke="#fffdf6" stroke-width="8"/>
    <path d="M550 0L567 81 601 115 643 170M206 205L257 183M180 480L250 467M342 580L344 760M434 637L424 760M570 653L565 760M675 357L900 357M682 489L900 489" stroke="#fffdf6" stroke-width="5"/></g>
    <rect width="900" height="760" fill="url(#map-grain)" pointer-events="none"/>
    <g class="map-landmarks"><text x="443" y="348" text-anchor="middle" class="lake-name">西 湖</text><text x="443" y="372" text-anchor="middle" class="lake-en">WEST LAKE</text><text x="338" y="122">北山街</text><text x="90" y="340" transform="rotate(-82 90 340)">杨 公 堤</text><text x="710" y="394" transform="rotate(90 710 394)">延 安 路</text><text x="405" y="678">南 山 路</text><text x="296" y="371" transform="rotate(80 296 371)">苏 堤</text><text x="498" y="432">三潭印月</text><text x="350" y="182">孤 山</text><text x="96" y="71">宝石山</text><text x="125" y="660">南屏山</text><text x="800" y="470">上 城 区</text></g>
    <polyline :points="points" fill="none" stroke="white" stroke-width="8" stroke-linejoin="round" opacity=".85"/>
    <polyline :points="points" fill="none" stroke="#377d67" stroke-width="3.5" stroke-linejoin="round" stroke-dasharray="7 7" class="route-line"/>
    <g v-for="(entry,index) in entries" :key="entry.activity.id" :transform="`translate(${entry.place.x},${entry.place.y})`" class="pin" role="button" tabindex="0" :aria-label="`选择${entry.place.name}`" @click.stop="emit('select',entry.activity.id)" @keydown.enter="emit('select',entry.activity.id)">
     <circle v-if="selected===entry.activity.id" r="32" fill="#377d67" opacity=".13"/>
     <circle r="19" :fill="selected===entry.activity.id?'#173f36':'#fffef9'" stroke="#fff" stroke-width="3" filter="url(#pinshadow)"/>
     <text text-anchor="middle" y="5" font-size="14" font-weight="700" :fill="selected===entry.activity.id?'#fff':'#326552'">{{index+1}}</text>
     <g :transform="`translate(${label(entry.place.id,index)[0]},${label(entry.place.id,index)[1]})`"><rect :x="label(entry.place.id,index)[2]" y="-15" width="130" height="29" rx="7" fill="#fffef9" opacity=".95"/><text :x="label(entry.place.id,index)[2]!+65" text-anchor="middle" y="4" font-size="12" fill="#35473e" font-weight="500">{{entry.place.name.split(' · ')[0]}}</text></g>
    </g>
   </g>
  </svg>
  <div class="map-compass"><Compass :size="23"/><span>N</span></div>
  <div class="map-tools"><button @click="terrain=!terrain" title="切换地图色调" aria-label="切换地图色调"><Layers :size="18"/></button><div class="zoom-tools"><button @click="zoom=Math.min(2.2,zoom+.2)" aria-label="放大地图"><Plus :size="18"/></button><button @click="zoom=Math.max(.65,zoom-.2)" aria-label="缩小地图"><Minus :size="18"/></button></div><button @click="zoom=1;offset={x:0,y:0}" aria-label="重置地图"><LocateFixed :size="18"/></button></div>
  <div class="map-bottom"><span><i/> 今日路线</span><span>西湖区域示意图 · 非导航地图</span></div>
 </div>
</template>

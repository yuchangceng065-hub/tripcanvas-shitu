import { ref, watch, onUnmounted, type Ref } from 'vue'
import { api, apiAvailable, checkAPI, backendConfigured, connection } from './api'
import { placeById, type Trip, type Mobility } from './domain'

export interface TransportSegment {
 mode:'walk'|'drive'|'transfer';label:string;distance:number;minutes:number;estimated:boolean
 geometry?:{type:'LineString';coordinates:[number,number][]};traffic?:string[]
}
export interface RoadRoute {
 mode:Trip['mode'];placeIds:string[];source:'osrm'|'amap';crs?:'GCJ02'
 legs:{distance:number;minutes:number;segments?:TransportSegment[]}[]
 geometry:{type:'LineString';coordinates:[number,number][]}
 waypoints:{location:[number,number];distance:number}[]
 locations?:Record<string,[number,number]>
 accessPoints?:{placeId:string;parking:import('./domain').Place;confirmed:boolean}[]
 accessConfirmed?:boolean;parkingByPlace?:Record<string,string>;parkingMinutes?:number;warnings?:string[]
 fetchedAt:number;expiresAt?:number;cached:boolean
}
export type RouteState = {status: 'loading'|'ready'|'error'|'empty'; route?: RoadRoute; error?: string}
export type RouteMap = Record<string, RoadRoute>
const ttl = 24 * 60 * 60 * 1000
export function parkingKey(mobility?:Mobility|null){return JSON.stringify([Object.entries(mobility?.parkingByPlace||{}).sort(([a],[b])=>a.localeCompare(b)),mobility?.parkingMinutes??5])}
export function routeKey(trip: Trip, day: number) {
  return (connection.routing||'osrm')+':v5:'+parkingKey(trip.mobility)+':'+trip.mode + ':' + (trip.days[day]?.activities.map(a => {const p=placeById(a.placeId);return `${a.placeId}@${p.lng},${p.lat}`}).join(';') || '')
}
export function matchesRoute(trip: Trip, day: number, route?: RoadRoute) {
  return !!route && (route.source!=='amap'||parkingKey({parkingByPlace:route.parkingByPlace||{},parkingMinutes:route.parkingMinutes??5})===parkingKey(trip.mobility)) && route.mode === trip.mode && route.placeIds.join(',') === trip.days[day]?.activities.map(a => a.placeId).join(',') && route.legs.length === Math.max(0, route.placeIds.length - 1)
}
function coordinate(p: unknown): p is [number, number] {
  return Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 90
}
function measurement(x: unknown): x is number {return typeof x === 'number' && Number.isFinite(x) && x >= 0}
export function validRoute(r: RoadRoute, mode: Trip['mode'], ids: string[]) {
  return ['osrm','amap'].includes(r?.source) && (r.source!=='amap'||validMixedRoute(r,ids)) && r.mode === mode && Array.isArray(r.placeIds) && r.placeIds.join(',') === ids.join(',') &&
    Array.isArray(r.legs) && r.legs.length === ids.length - 1 && r.legs.every(l => measurement(l.distance) && measurement(l.minutes)) &&
    r.geometry?.type === 'LineString' && Array.isArray(r.geometry.coordinates) && r.geometry.coordinates.length >= 2 && r.geometry.coordinates.every(coordinate) &&
    Array.isArray(r.waypoints) && r.waypoints.length === ids.length && r.waypoints.every(w => coordinate(w.location) && measurement(w.distance)) && measurement(r.fetchedAt)
}
function validMixedRoute(r:RoadRoute,ids:string[]){
 if(r.crs!=='GCJ02'||!r.locations||!ids.every(id=>coordinate(r.locations![id]))||typeof r.accessConfirmed!=='boolean')return false
 if(!r.legs?.every(leg=>Array.isArray(leg.segments)&&leg.segments.every(s=>['walk','drive','transfer'].includes(s.mode)&&measurement(s.distance)&&measurement(s.minutes)&&(s.mode==='transfer'||s.geometry?.type==='LineString'&&s.geometry.coordinates.length>=2&&s.geometry.coordinates.every(coordinate)))&&Math.abs(leg.segments.reduce((n,s)=>n+s.distance,0)-leg.distance)<.001&&leg.segments.reduce((n,s)=>n+s.minutes,0)===leg.minutes))return false
 return Array.isArray(r.accessPoints)&&(r.mode==='walk'||[...new Set(ids)].every(id=>r.accessPoints!.some(a=>a.placeId===id&&a.parking?.provider==='amap'&&a.parking.crs==='GCJ02'&&coordinate([a.parking.lng,a.parking.lat]))))
}
function learnCoordinates(r:RoadRoute){if(r.source==='amap')for(const [id,location] of Object.entries(r.locations||{})){const p=placeById(id);if(p)p.routingLocation=location}}
export function normalizeOSRM(data: any, mode: Trip['mode'], placeIds: string[]): RoadRoute {
  if (data?.code === 'NoRoute' || data?.code === 'NoSegment') throw new Error('这些地点之间暂无道路路线，请调整地点或交通方式')
  if (data?.code !== 'Ok' || !data.routes?.[0]) throw new Error('道路服务未返回有效路线')
  const raw = data.routes[0]
  const route: RoadRoute = {mode,placeIds,source:'osrm',geometry:raw.geometry,waypoints:data.waypoints,
    legs:raw.legs?.map((l: any) => ({distance:measurement(l.distance) ? l.distance / 1000 : NaN,minutes:measurement(l.duration) ? Math.ceil(l.duration / 60) : NaN})),
    fetchedAt:Date.now(),cached:false}
  if (!validRoute(route,mode,placeIds)) throw new Error('道路服务返回了不完整的路线')
  return route
}
const cache = new Map<string,RoadRoute>()
const inFlight = new Map<string,Promise<RoadRoute>>()
let publicQueue: Promise<unknown> = Promise.resolve(), publicNextAt = 0
function cachedRoute(key: string, mode: Trip['mode'], ids: string[]) {
  let value = cache.get(key)
  if (!value) try {value = JSON.parse(localStorage.getItem('tripcanvas.routes.v5.' + key) || 'null')} catch {}
  if (value && value.source===connection.routing && validRoute(value,mode,ids) && Date.now() < (value.expiresAt??value.fetchedAt+ttl)) return {...value,cached:true}
}
async function fetchRoute(mode: Trip['mode'], ids: string[], mobility?:Mobility|null, signal?: AbortSignal): Promise<RoadRoute> {
  await checkAPI()
  signal?.throwIfAborted()
  // Configured production backends never silently fall back to a public provider.
  if (apiAvailable || backendConfigured) {
    const value: RoadRoute = await api('/routes','POST',{mode,placeIds:ids,...(mobility||{})},signal)
    if (!validRoute(value,mode,ids)) throw new Error('后端返回的路线不完整')
    return value
  }
  // Owner-private preview fallback, until the Railway service is connected.
  // Serialize requests, cache and debounce; public multi-user rollout must use the API.
  const request = publicQueue.catch(() => {}).then(async () => {
    signal?.throwIfAborted()
    await new Promise(resolve => setTimeout(resolve,Math.max(0,publicNextAt - Date.now())))
    signal?.throwIfAborted()
    publicNextAt = Date.now() + 1100
    const profile = mode === 'walk' ? 'foot' : 'car'
    const coords = ids.map(id => {const p = placeById(id);return `${p.lng},${p.lat}`}).join(';')
    const params = new URLSearchParams({overview:'full',geometries:'geojson',steps:'false',alternatives:'false',generate_hints:'false',radiuses:ids.map(() => mode === 'walk' ? '300' : '1000').join(';')})
    const response = await fetch(`https://routing.openstreetmap.de/routed-${profile}/route/v1/driving/${coords}?${params}`,{signal:signal ? AbortSignal.any([signal,AbortSignal.timeout(25000)]) : AbortSignal.timeout(25000)})
    if (response.status === 429) {publicNextAt = Date.now() + 60000;throw new Error('路线服务繁忙，请一分钟后重试')}
    const data = await response.json()
    if (!response.ok && !['NoRoute','NoSegment'].includes(data?.code)) throw new Error('道路路线暂时无法加载，请重试')
    return normalizeOSRM(data,mode,ids)
  })
  publicQueue = request
  return request
}
export async function loadRoute(trip: Trip, day: number, signal?: AbortSignal): Promise<RoadRoute> {
  const key = routeKey(trip,day), ids = trip.days[day]!.activities.map(a => a.placeId)
  const cached = cachedRoute(key,trip.mode,ids)
  if (cached) {learnCoordinates(cached);return cached}
  // Shared requests have no consumer-owned AbortController; stale responses are ignored by the watcher.
  if (inFlight.has(key)) return inFlight.get(key)!
  const pending = fetchRoute(trip.mode,ids,trip.mobility,signal).then(value => {
    learnCoordinates(value);cache.set(key,value)
    try {
      const prefix='tripcanvas.routes.v5.',serialized=JSON.stringify(value)
      // Bound persistent geometry storage so route caching cannot crowd out plans.
      for(const old of Object.keys(localStorage).filter(k=>/^tripcanvas\.routes\.v[24]\./.test(k)))localStorage.removeItem(old)
      const keys=Object.keys(localStorage).filter(k=>k.startsWith(prefix)&&k!==prefix+key)
      let size=keys.reduce((n,k)=>n+(localStorage.getItem(k)?.length||0)*2,0)
      while(keys.length&&(keys.length>=4||size+serialized.length*2>1000000)){const old=keys.shift()!;size-=(localStorage.getItem(old)?.length||0)*2;localStorage.removeItem(old)}
      if(serialized.length*2<=500000)localStorage.setItem(prefix+key,serialized)
    } catch {}
    return value
  }).finally(() => inFlight.delete(key))
  inFlight.set(key,pending)
  return pending
}
export function useRoadRoutes(trip: Ref<Trip>, activeDay: Ref<number>) {
  const routes = ref<RouteMap>({}), state = ref<RouteState>({status:'loading'}), retry = ref(0)
  let generation = 0, timer: ReturnType<typeof setTimeout>
  const stop = watch(() => [routeKey(trip.value,activeDay.value),retry.value],() => {
    const version = ++generation
    clearTimeout(timer)
    const snapshot = JSON.parse(JSON.stringify(trip.value)) as Trip, day = activeDay.value, key = routeKey(snapshot,day)
    if (snapshot.days[day]!.activities.length < 2) {state.value = {status:'empty'};return}
    const cached = cachedRoute(key,snapshot.mode,snapshot.days[day]!.activities.map(a => a.placeId))
    if (cached) {learnCoordinates(cached);routes.value[key] = cached;state.value = {status:'ready',route:cached};return}
    delete routes.value[key]
    state.value = {status:'loading'}
    timer = setTimeout(async () => {
      try {
        const value = await loadRoute(snapshot,day)
        if (version !== generation) return
        routes.value[key] = value
        state.value = {status:'ready',route:value}
      } catch (e) {
        if (version === generation) state.value = {status:'error',error:e instanceof Error && e.name !== 'TimeoutError' ? e.message : '路线请求超时，请稍后重试'}
      }
    },450)
  },{immediate:true})
  onUnmounted(() => {stop();generation++;clearTimeout(timer)})
  return {routes,state,refresh:() => retry.value++}
}

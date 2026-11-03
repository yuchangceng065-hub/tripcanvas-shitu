import { ref, watch, onUnmounted, type Ref } from 'vue'
import { api, apiAvailable, checkAPI } from './api'
import { placeById, type Trip } from './domain'

export interface RoadRoute {
  mode: Trip['mode']; placeIds: string[]; source: 'osrm'
  legs: {distance: number; minutes: number}[]
  geometry: {type: 'LineString'; coordinates: [number, number][]}
  waypoints: {location: [number, number]; distance: number}[]
  fetchedAt: number; cached: boolean
}
export type RouteState = {status: 'loading'|'ready'|'error'|'empty'; route?: RoadRoute; error?: string}
export type RouteMap = Record<string, RoadRoute>
const ttl = 24 * 60 * 60 * 1000
export function routeKey(trip: Trip, day: number) {
  return trip.mode + ':' + (trip.days[day]?.activities.map(a => {const p=placeById(a.placeId);return `${a.placeId}@${p.lng},${p.lat}`}).join(';') || '')
}
export function matchesRoute(trip: Trip, day: number, route?: RoadRoute) {
  return !!route && route.mode === trip.mode && route.placeIds.join(',') === trip.days[day]?.activities.map(a => a.placeId).join(',') && route.legs.length === Math.max(0, route.placeIds.length - 1)
}
function coordinate(p: unknown): p is [number, number] {
  return Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 90
}
function measurement(x: unknown): x is number {return typeof x === 'number' && Number.isFinite(x) && x >= 0}
export function validRoute(r: RoadRoute, mode: Trip['mode'], ids: string[]) {
  return r?.source === 'osrm' && r.mode === mode && Array.isArray(r.placeIds) && r.placeIds.join(',') === ids.join(',') &&
    Array.isArray(r.legs) && r.legs.length === ids.length - 1 && r.legs.every(l => measurement(l.distance) && measurement(l.minutes)) &&
    r.geometry?.type === 'LineString' && Array.isArray(r.geometry.coordinates) && r.geometry.coordinates.length >= 2 && r.geometry.coordinates.every(coordinate) &&
    Array.isArray(r.waypoints) && r.waypoints.length === ids.length && r.waypoints.every(w => coordinate(w.location) && measurement(w.distance)) && measurement(r.fetchedAt)
}
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
  if (!value) try {value = JSON.parse(localStorage.getItem('tripcanvas.routes.v2.' + key) || 'null')} catch {}
  if (value && validRoute(value,mode,ids) && Date.now() - value.fetchedAt < ttl) return {...value,cached:true}
}
async function fetchRoute(mode: Trip['mode'], ids: string[], signal?: AbortSignal): Promise<RoadRoute> {
  await checkAPI()
  signal?.throwIfAborted()
  // Configured production backends never silently fall back to a public provider.
  if (apiAvailable || import.meta.env.VITE_API_BASE) {
    const value: RoadRoute = await api('/routes','POST',{mode,placeIds:ids},signal)
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
  if (cached) return cached
  // Shared requests have no consumer-owned AbortController; stale responses are ignored by the watcher.
  if (inFlight.has(key)) return inFlight.get(key)!
  const pending = fetchRoute(trip.mode,ids,signal).then(value => {
    cache.set(key,value)
    try {
      localStorage.setItem('tripcanvas.routes.v2.' + key,JSON.stringify(value))
      const keys = Object.keys(localStorage).filter(k => k.startsWith('tripcanvas.routes.v2.'))
      for (const old of keys.slice(0,Math.max(0,keys.length - 32))) localStorage.removeItem(old)
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
    if (cached) {routes.value[key] = cached;state.value = {status:'ready',route:cached};return}
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

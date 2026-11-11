import { defineStore } from "pinia";
import { ref, watch } from "vue";
import {
  clone,
  createTrip,
  uid,
  registerPlaces,
  capturePlaces,
  type Trip,
} from "./domain";
export interface Version {
  id: string;
  date: string;
  label: string;
  trip: Trip;
}
const KEY = "tripcanvas.v1";
function read() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (
      value?.trips?.length &&
      value.trips.every((t: Trip) => t.days?.length)
    ) {
      for (const t of value.trips) registerPlaces(t.placeSnapshots);
      for (const versions of Object.values(value.versions || {}) as Version[][])
        for (const v of versions) registerPlaces(v.trip.placeSnapshots);
      return value;
    }
  } catch {}
  return null;
}
export const useTrips = defineStore("trips", () => {
  const saved = read();
  const trips = ref<Trip[]>(saved?.trips || [createTrip()]);
  const activeId = ref<string>(saved?.activeId || trips.value[0]!.id);
  const versions = ref<Record<string, Version[]>>(saved?.versions || {});
  const favorites = ref<string[]>(
    saved?.favorites || ["quyuan", "coffee", "art"],
  );
  const storageError = ref(false);
  function persist() {
    try {
      localStorage.setItem(
        KEY,
        JSON.stringify({
          trips: trips.value,
          activeId: activeId.value,
          versions: versions.value,
          favorites: favorites.value,
        }),
      );
      storageError.value = false;
    } catch {
      storageError.value = true;
    }
  }
  watch([trips, activeId, versions, favorites], persist, { deep: true });
  function record(trip: Trip, label: string) {
    const v = versions.value[trip.id] || [];
    v.unshift({
      id: uid(),
      date: new Date().toISOString(),
      label,
      trip: clone(trip),
    });
    versions.value[trip.id] = v.slice(0, 30);
  }
  if (!versions.value[trips.value[0]!.id]) record(trips.value[0]!, "初始行程");
  function commit(
    next: Trip,
    label: string,
    expected: number,
    verified = false,
  ) {
    const index = trips.value.findIndex((t) => t.id === next.id);
    if (index < 0) throw new Error("旅行已不存在");
    if (trips.value[index]!.revision !== expected)
      throw new Error("行程已有新修改，请重新生成建议");
    capturePlaces(next);
    next.validationStatus = verified ? "verified" : "draft";
    next.revision = expected + 1;
    trips.value[index] = clone(next);
    record(next, label);
  }
  // Replace a local copy with an authoritative cloud version. The displaced
  // local draft is kept as a history entry so a slow-confirm race can never
  // silently destroy an edit the user made while the request was in flight.
  function replaceRemote(next: Trip, label: string) {
    const index = trips.value.findIndex((t) => t.id === next.id);
    if (index < 0) throw new Error("旅行已不存在");
    record(trips.value[index]!, label + " · 替换前的本地版本");
    capturePlaces(next);
    trips.value[index] = clone(next);
    record(next, label);
  }
  function add(trip: Trip) {
    capturePlaces(trip);
    trips.value.push(trip);
    activeId.value = trip.id;
    record(trip, "创建旅行");
  }
  function remove(id: string) {
    trips.value = trips.value.filter((t) => t.id !== id);
    delete versions.value[id];
    if (!trips.value.length) add(createTrip());
    activeId.value = trips.value[0]!.id;
  }
  function toggleFavorite(id: string) {
    favorites.value = favorites.value.includes(id)
      ? favorites.value.filter((x) => x !== id)
      : [...favorites.value, id];
  }
  return {
    trips,
    activeId,
    versions,
    favorites,
    storageError,
    commit,
    add,
    remove,
    toggleFavorite,
    replaceRemote,
    persist,
  };
});

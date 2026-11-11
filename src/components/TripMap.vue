<script setup lang="ts">
import { ref } from "vue";
import AmapTripMap from "./AmapTripMap.vue";
import OsmTripMap from "./OsmTripMap.vue";
import { connection } from "../api";
import type { Entry } from "../domain";
import type { RouteState } from "../routes";
defineProps<{
  entries: Entry[];
  selected: string;
  state: RouteState;
  mode: "walk" | "drive";
}>();
const emit = defineEmits<{ select: [id: string]; retry: [] }>();
const amapUnavailable = ref(false);
</script>
<template>
  <AmapTripMap
    v-if="connection.map === 'amap' && !amapUnavailable"
    :entries="entries"
    :selected="selected"
    :state="state"
    :mode="mode"
    @select="emit('select', $event)"
    @retry="emit('retry')"
    @unavailable="amapUnavailable = true"
  /><OsmTripMap
    v-else
    :entries="entries"
    :selected="selected"
    :state="state"
    :mode="mode"
    :amap-unavailable="amapUnavailable"
    @select="emit('select', $event)"
    @retry="emit('retry')"
  />
</template>

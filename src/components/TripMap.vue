<script setup lang="ts">
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
</script>
<template>
  <AmapTripMap
    v-if="connection.map === 'amap'"
    :entries="entries"
    :selected="selected"
    :state="state"
    :mode="mode"
    @select="emit('select', $event)"
    @retry="emit('retry')"
  /><OsmTripMap
    v-else
    :entries="entries"
    :selected="selected"
    :state="state"
    :mode="mode"
    @select="emit('select', $event)"
    @retry="emit('retry')"
  />
</template>

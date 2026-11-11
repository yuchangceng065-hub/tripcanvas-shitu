<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api, confirmTrip } from "../api";
import { clone, registerPlaces, schedule, time, type Trip } from "../domain";
import type { RoadRoute } from "../routes";
const props = defineProps<{ trip: Trip; loggedIn: boolean }>();
const emit = defineEmits<{ confirmed: [trip: Trip]; login: [] }>();
const preview = ref<{
  issues: { message: string; severity: string }[];
  routes: Record<string, RoadRoute>;
} | null>(null);
const busy = ref(false),
  error = ref("");
const signature = computed(() => JSON.stringify(props.trip));
let generation = 0;
watch(signature, () => {
  generation++;
  preview.value = null;
  error.value = "行程已修改，请重新核验当前草稿。";
});
const hardErrors = computed(
  () =>
    preview.value?.issues.some((issue) => issue.severity === "error") ?? true,
);
async function check() {
  if (busy.value || !props.loggedIn) return;
  const current = ++generation,
    before = clone(props.trip),
    expected = signature.value;
  busy.value = true;
  error.value = "";
  preview.value = null;
  try {
    await api("/trips/" + before.id, "PUT", { trip: before });
    const result = await api("/planner/preview", "POST", { trip: before });
    // The watcher may not have flushed yet when a slow request resolves, so
    // compare the actual content as well as the generation counter.
    if (current === generation && signature.value === expected)
      preview.value = result;
  } catch (e) {
    if (current === generation) error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function confirm() {
  if (!preview.value || hardErrors.value || busy.value) return;
  const current = generation,
    before = clone(props.trip),
    expected = signature.value;
  busy.value = true;
  error.value = "";
  try {
    const result = await confirmTrip(before, before.revision);
    if (current !== generation || signature.value !== expected) {
      preview.value = null;
      error.value = "核验期间行程已变化，请重新打开云端版本后再核验。";
      return;
    }
    registerPlaces(result.trip.placeSnapshots);
    emit("confirmed", result.trip);
  } catch (e) {
    preview.value = null;
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(() => void check());
</script>
<template>
  <section aria-label="草稿整程核验">
    <h2>核验当前行程</h2>
    <p class="modal-description">
      调整先保存为草稿。核对整程道路、逐站时间和冲突后，再确认这个版本。
    </p>
    <p v-if="!loggedIn" class="form-hint">登录后可核验全部日期并确认到云端。</p>
    <button v-if="!loggedIn" class="button primary" @click="emit('login')">
      登录后核验
    </button>
    <p v-if="busy" role="status">正在核验道路与行程条件…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <template v-if="preview">
      <p v-if="!hardErrors" class="check-success">
        整程道路与时间检查通过，请核对以下安排。
      </p>
      <p
        v-for="(issue, index) in preview.issues"
        :key="index"
        :class="issue.severity === 'error' ? 'form-error' : 'form-hint'"
      >
        {{ issue.message }}
      </p>
      <section
        v-for="(day, index) in trip.days"
        :key="day.date"
        class="agent-preview-day"
      >
        <h3>Day {{ index + 1 }} · {{ day.date }}</h3>
        <ol>
          <li
            v-for="row in schedule(trip, index, preview.routes[index])"
            :key="row.activity.id"
          >
            <time>{{ time(row.start) }}—{{ time(row.end) }}</time>
            {{ row.place.name
            }}<small v-if="row.travel"> · 转场 {{ row.travel }} 分钟</small>
          </li>
        </ol>
      </section>
      <p class="form-hint">
        本次道路估时用于核验当前版本。实际路况、营业和预约请在出行前确认。
      </p>
    </template>
    <div class="modal-actions">
      <button
        v-if="loggedIn"
        class="button quiet"
        @click="check"
        :disabled="busy"
      >
        重新核验
      </button>
      <button
        v-if="loggedIn"
        class="button primary"
        @click="confirm"
        :disabled="busy || !preview || hardErrors"
      >
        确认已核验版本
      </button>
    </div>
  </section>
</template>

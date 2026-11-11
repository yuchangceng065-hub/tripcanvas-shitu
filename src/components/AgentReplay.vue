<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { api } from "../api";
import { placeById } from "../domain";
const props = defineProps<{ runId?: string }>();
interface Event {
  seq: number;
  phase: string;
  status: string;
  createdAt?: number;
  detail: Record<string, unknown>;
}
interface Run {
  runId: string;
  status: string;
  events: Event[];
  modelUsage?: {
    providerRequests: number;
    totalTokens: number;
    tokensComplete: boolean;
  };
}
const runs = ref<
    { runId: string; created: number; kind: string; status: string }[]
  >([]),
  selected = ref(props.runId || ""),
  run = ref<Run | null>(null),
  index = ref(0),
  error = ref(""),
  loading = ref(false),
  playing = ref(false);
let timer: ReturnType<typeof setInterval> | undefined,
  serial = 0;
const labels: Record<string, string> = {
  graph_node: "LangGraph 节点",
  graph_pause: "保存检查点",
  graph_resume: "恢复检查点",
  mcp_call: "MCP 工具调用",
  requirements: "接收条件",
  understanding: "理解输入",
  place_search: "搜索真实地点",
  model: "模型选择动作",
  tool_call: "调用工具",
  tool_result: "取得核验结果",
  model_error: "模型结构修复",
  search: "比较候选",
  validation: "核验道路与约束",
  clarification: "请求确认",
  needs_input: "等待补充",
  needs_confirmation: "等待需求确认",
  resumed: "接回原任务",
  completed: "完成",
  failed: "失败",
  interrupted: "服务中断",
};
const current = computed(() => run.value?.events[index.value]);
const detail = computed(() =>
  JSON.stringify(
    current.value?.detail ?? {},
    (key, value) =>
      key === "placeIds" && Array.isArray(value)
        ? value.map((id) => placeById(id)?.name || id)
        : value,
    2,
  ),
);
const gap = computed(() => {
  const a = run.value?.events[index.value - 1]?.createdAt,
    b = current.value?.createdAt;
  return a && b ? Math.max(0, b - a).toFixed(2) : null;
});
function stop() {
  playing.value = false;
  clearInterval(timer);
}
function seek(value: number) {
  stop();
  index.value = value;
}
function play() {
  if (!run.value?.events.length) return;
  if (index.value === run.value.events.length - 1) index.value = 0;
  playing.value = true;
  timer = setInterval(() => {
    if (index.value < (run.value?.events.length ?? 0) - 1) index.value++;
    else stop();
  }, 1000);
}
async function load() {
  stop();
  error.value = "";
  const ticket = ++serial;
  loading.value = true;
  run.value = null;
  try {
    const result = await api("/runs/" + selected.value);
    if (ticket !== serial) return;
    run.value = result;
    index.value = 0;
  } catch (e) {
    if (ticket === serial) error.value = (e as Error).message;
  } finally {
    if (ticket === serial) loading.value = false;
  }
}
onMounted(async () => {
  try {
    runs.value = await api("/runs");
    if (!selected.value) selected.value = runs.value[0]?.runId || "";
    if (selected.value) await load();
  } catch (e) {
    error.value = (e as Error).message;
  }
});
watch(
  () => props.runId,
  (value) => {
    if (value) {
      selected.value = value;
      void load();
    }
  },
);
onUnmounted(() => {
  serial++;
  stop();
});
const runKindLabels: Record<string, string> = {
  simulation: "行程推演",
  guide_import: "攻略导入",
  conversation: "多轮对话",
  agent: "Agent 规划",
};
</script>
<template>
  <section class="execution-replay" aria-label="Agent 执行回放">
    <span class="eyebrow">EVERY STEP HAS A TRACE</span>
    <h2>看看方案是怎样产生的</h2>
    <p class="form-hint">
      回放服务端真实事件，查看工具结果和失败记录。播放只改变查看位置，不重新执行任务。
    </p>
    <label
      >选择运行记录<select v-model="selected" @change="load">
        <option v-for="r in runs" :key="r.runId" :value="r.runId">
          {{ new Date(r.created * 1000).toLocaleString("zh-CN") }} ·
          {{ runKindLabels[r.kind] || r.kind }} · {{ r.status }}
        </option>
      </select></label
    >
    <p v-if="loading" role="status">正在读取真实运行记录…</p>
    <p v-if="error" class="form-error" role="alert">
      {{ error }} <button class="text-button" @click="load">重新读取</button>
    </p>
    <p v-if="!runs.length && !loading && !error" class="form-hint">
      完成一次规划、攻略导入或行程推演后，就能在这里回放。
    </p>
    <template v-if="run">
      <div class="replay-controls">
        <button
          class="button quiet small"
          @click="playing ? stop() : play()"
          :disabled="!run.events.length"
        >
          {{ playing ? "暂停" : "播放执行记录" }}</button
        ><button
          class="button quiet small"
          @click="seek(Math.max(0, index - 1))"
          :disabled="!index"
        >
          上一步</button
        ><button
          class="button quiet small"
          @click="seek(Math.min(run.events.length - 1, index + 1))"
          :disabled="index >= run.events.length - 1"
        >
          下一步</button
        ><span
          >{{ index + 1 }} / {{ run.events.length }} · {{ run.status }}</span
        >
      </div>
      <input
        aria-label="执行回放进度"
        type="range"
        min="0"
        :max="Math.max(0, run.events.length - 1)"
        :value="index"
        @input="seek(Number(($event.target as HTMLInputElement).value))"
      />
      <div class="replay-layout">
        <ol class="replay-steps">
          <li v-for="(event, i) in run.events" :key="event.seq">
            <button
              :class="{
                active: i === index,
                past: i < index,
                error: event.status === 'error' || event.phase === 'failed',
              }"
              @click="seek(i)"
            >
              <span>{{ event.seq }}</span
              >{{ labels[event.phase] || event.phase }}
            </button>
          </li>
        </ol>
        <article class="replay-detail" v-if="current">
          <small
            >事件 #{{ current.seq
            }}<template v-if="current.createdAt">
              ·
              {{
                new Date(current.createdAt * 1000).toLocaleTimeString("zh-CN")
              }}</template
            ></small
          >
          <h3>{{ labels[current.phase] || current.phase }}</h3>
          <p v-if="gap !== null" class="form-hint">
            距离上一条记录 {{ gap }} 秒（包含处理与等待）
          </p>
          <pre>{{ detail }}</pre>
          <p class="form-hint">
            仅展示已记录的动作与工具摘要；不包含模型隐藏推理。
          </p>
        </article>
      </div>
      <p v-if="run.modelUsage" class="search-evidence">
        本任务累计模型请求 {{ run.modelUsage.providerRequests }} 次 ·
        {{ run.modelUsage.totalTokens }} token{{
          run.modelUsage.tokensComplete ? "" : "（部分调用未报告用量）"
        }}
      </p>
    </template>
  </section>
</template>

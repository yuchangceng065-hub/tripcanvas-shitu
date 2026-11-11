// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApp, nextTick, h, reactive, type App } from "vue";
import AgentTrace from "./components/AgentTrace.vue";
import { startAgent, watchRun, confirmTrip, answerConversation } from "./api";
import { places, type Requirements, type Trip } from "./domain";
vi.mock("./api", () => ({
  startAgent: vi.fn(),
  watchRun: vi.fn(),
  confirmTrip: vi.fn(),
  answerConversation: vi.fn(),
}));
const startMock = vi.mocked(startAgent),
  watchMock = vi.mocked(watchRun),
  confirmMock = vi.mocked(confirmTrip),
  answerMock = vi.mocked(answerConversation);
const requirements: Requirements = {
  city: "杭州",
  startDate: "2026-11-14",
  days: 1,
  people: 2,
  mode: "walk",
  start: 570,
  end: 1110,
  pace: "balanced",
  interests: [],
};
const trip = (): Trip => ({
  id: "agent-qa",
  title: "测试杭州",
  city: "杭州",
  people: 2,
  mode: "walk",
  revision: 1,
  days: [
    {
      date: "2026-11-14",
      start: 570,
      end: 1110,
      activities: [
        { id: "a", placeId: "quyuan", duration: 90, lock: "keep" },
        { id: "b", placeId: "museum", duration: 90, lock: "keep" },
      ],
    },
  ],
});
const plan = () => ({
  trip: trip(),
  issues: [],
  metrics: {
    walkingKm: 0.6,
    travelMinutes: 10,
    visitMinutes: 180,
    freeMinutes: 350,
    days: [{ walkingKm: 0.6, freeMinutes: 350 }],
  },
  summary: "两站慢走",
  timeline: [
    {
      date: "2026-11-14",
      start: 570,
      end: 1110,
      activities: [
        {
          activityId: "a",
          placeId: "quyuan",
          start: 570,
          end: 660,
          travelMinutes: 0,
          waitingMinutes: 0,
          duration: 90,
          lock: "keep",
          fixedStart: null,
        },
        {
          activityId: "b",
          placeId: "museum",
          start: 670,
          end: 760,
          travelMinutes: 10,
          waitingMinutes: 0,
          duration: 90,
          lock: "keep",
          fixedStart: null,
        },
      ],
    },
  ],
  includedIds: ["quyuan", "museum"],
  omittedIds: ["silk"],
  routeSources: ["amap"],
});
let app: App,
  host: HTMLDivElement,
  created: Trip | null,
  input: {
    title: string;
    requirements: Requirements;
    candidateIds: string[];
    optionalIds: string[];
    loggedIn: boolean;
    initialRunId?: string;
  };
const flush = async () => {
  for (let i = 0; i < 4; i++) {
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
  }
};
const button = (text: string) =>
  [...host.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  )!;
function mount() {
  app = createApp({
    render: () =>
      h(AgentTrace, {
        ...input,
        onCreated: (value: Trip) => {
          created = value;
        },
      }),
  });
  app.mount(host);
}
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  created = null;
  host = document.createElement("div");
  document.body.append(host);
  input = reactive({
    title: "测试杭州",
    requirements: { ...requirements },
    candidateIds: ["quyuan", "museum", "silk"],
    optionalIds: ["silk"],
    loggedIn: true,
  });
  startMock.mockResolvedValue({ runId: "r1", status: "running" });
  answerMock.mockResolvedValue({ runId: "r1", status: "running" });
  watchMock.mockImplementation(async (_id, onEvent) => {
    onEvent({
      seq: 1,
      phase: "tool_call",
      status: "ok",
      detail: { step: 1, tool: "route_day" },
    });
    onEvent({ seq: 2, phase: "completed", status: "ok", detail: {} });
    return { status: "completed", validation: plan() };
  });
  confirmMock.mockResolvedValue({
    trip: trip(),
    revision: 1,
    issues: [],
    idempotent: false,
  });
});
afterEach(() => {
  app?.unmount();
  host.remove();
  sessionStorage.clear();
});
it("显示真实逐站时间、锁定、来源和地点取舍，明确确认才保存", async () => {
  mount();
  await flush();
  expect(host.textContent).toContain("正在查询道路");
  expect(host.textContent).toContain("曲院风荷");
  expect(host.textContent).toContain("浙江省博物馆");
  expect(host.textContent).toContain("11:10—12:40");
  expect(host.textContent).toContain("2026-11-14");
  expect(host.textContent).toContain("必须保留");
  expect(host.textContent).toContain("舍弃可选地点：中国丝绸博物馆");
  expect(host.textContent).toContain("高德");
  expect(confirmMock).not.toHaveBeenCalled();
  expect(startMock).toHaveBeenCalledWith(
    expect.objectContaining({ title: "测试杭州", optionalIds: ["silk"] }),
    expect.any(AbortSignal),
  );
  button("确认并保存 Agent 方案").click();
  await flush();
  expect(confirmMock).toHaveBeenCalledWith(
    expect.objectContaining({ id: "agent-qa", title: "测试杭州" }),
    0,
    "r1",
  );
  expect(created).toBeTruthy();
});
it("规划解释采用最终方案确认的需求，不沿用运行开始时的表单", async () => {
  const initial: Requirements = {
    ...requirements,
    mustVisitIds: ["quyuan"],
    destinationPlaceId: "station",
    returnBy: 1020,
  };
  input.requirements = initial;
  const finalTrip = trip();
  finalTrip.days = [
    {
      date: "2026-11-14",
      start: 570,
      end: 1110,
      activities: [
        { id: "a", placeId: "quyuan", duration: 90, lock: "keep" },
        {
          id: "b",
          placeId: "station",
          duration: 15,
          lock: "fixed",
          fixedStart: 1020,
        },
      ],
    },
  ];
  finalTrip.requirements = {
    ...initial,
    mustVisitIds: ["quyuan", "museum"],
    returnBy: 1000,
  };
  const finalPlan = {
    ...plan(),
    trip: finalTrip,
    includedIds: ["quyuan", "museum"],
    omittedIds: ["silk"],
    timeline: [
      {
        date: "2026-11-14",
        start: 570,
        end: 1110,
        activities: [
          {
            activityId: "a",
            placeId: "quyuan",
            start: 570,
            end: 660,
            travelMinutes: 0,
            waitingMinutes: 0,
            duration: 90,
            lock: "keep",
            fixedStart: null,
          },
          {
            activityId: "b",
            placeId: "station",
            start: 1005,
            end: 1020,
            travelMinutes: 10,
            waitingMinutes: 15,
            duration: 15,
            lock: "fixed",
            fixedStart: null,
          },
        ],
      },
    ],
  };
  watchMock.mockResolvedValueOnce({
    status: "completed",
    validation: finalPlan,
  });
  mount();
  await flush();
  const reasons =
    host.querySelector('[aria-label="规划依据"]')?.textContent ?? "";
  expect(reasons).toContain("曲院风荷");
  expect(reasons).toContain("浙江省博物馆");
  expect(reasons).toContain("提前 10 分钟");
  expect(reasons).not.toContain("提前 30 分钟");
});
it("连接失败后继续查看相同运行，不重新创建或消耗生成次数", async () => {
  watchMock.mockRejectedValueOnce(new Error("连接中断"));
  mount();
  await flush();
  expect(button("继续查看本次运行")).toBeTruthy();
  button("继续查看本次运行").click();
  await flush();
  expect(startMock).toHaveBeenCalledTimes(1);
  expect(watchMock).toHaveBeenCalledTimes(2);
  expect(watchMock.mock.calls.every((args) => args[0] === "r1")).toBe(true);
  expect(host.textContent).toContain("测试杭州");
  expect(confirmMock).not.toHaveBeenCalled();
});
it("重新打开相同需求时恢复保存的运行编号", async () => {
  sessionStorage.setItem(
    "tripcanvas.agent.pending.v1",
    JSON.stringify({
      runId: "saved-run",
      signature: JSON.stringify([
        input.title,
        input.requirements,
        input.candidateIds,
        input.optionalIds,
        null,
      ]),
    }),
  );
  mount();
  await flush();
  expect(startMock).not.toHaveBeenCalled();
  expect(watchMock.mock.calls[0]?.[0]).toBe("saved-run");
});
it("需求变更使旧方案失效，不能继续保存旧结果", async () => {
  mount();
  await flush();
  expect(button("确认并保存 Agent 方案")).toBeTruthy();
  input.candidateIds = ["quyuan", "museum"];
  await flush();
  expect(button("确认并保存 Agent 方案")).toBeUndefined();
  expect(host.textContent).toContain("需求或候选已修改");
  expect(confirmMock).not.toHaveBeenCalled();
});
it("缺少逐站时间或存在硬冲突时不能确认保存", async () => {
  watchMock.mockResolvedValueOnce({
    status: "completed",
    validation: { ...plan(), timeline: [] },
  });
  mount();
  await flush();
  expect(button("确认并保存 Agent 方案")).toBeUndefined();
  expect(host.textContent).toContain("缺少完整逐站预览");
  watchMock.mockResolvedValueOnce({
    status: "completed",
    validation: {
      ...plan(),
      issues: [{ severity: "error", message: "返程晚到" }],
    },
  });
  button("重新运行").click();
  await flush();
  expect(button("确认并保存 Agent 方案").disabled).toBe(true);
});

const conversation = (version = 1, review = false) => ({
  version,
  status: review ? "review" : "needs_input",
  phase: "intake" as const,
  messages: [{ role: "assistant" as const, text: "请确认地点，再补充想法。" }],
  requirements: { ...requirements },
  mentions: [
    {
      key: "q",
      text: "曲院",
      role: "visit" as const,
      required: true,
      candidates: [{ ...places.find((p) => p.id === "quyuan")! }],
      selectedId: review ? "quyuan" : undefined,
    },
  ],
  missing: review ? [] : ["place:q"],
  question: "请确认地点",
  notes: [],
  canConfirm: review,
  remainingSteps: 12,
  remainingReplies: 6,
});
it("回答反问后继续原任务，Enter 发送且不重复启动", async () => {
  watchMock.mockResolvedValueOnce({
    status: "needs_input",
    conversation: { ...conversation(), phase: "agent", mentions: [] },
  });
  mount();
  await flush();
  const answer = host.querySelector<HTMLTextAreaElement>("textarea")!;
  answer.value = "先去湖边";
  answer.dispatchEvent(new Event("input"));
  await nextTick();
  answer.dispatchEvent(
    new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    }),
  );
  await flush();
  expect(answerMock).toHaveBeenCalledWith(
    "r1",
    expect.objectContaining({
      version: 1,
      text: "先去湖边",
      requestId: expect.any(String),
    }),
  );
  expect(startMock).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("11:10—12:40");
  expect(confirmMock).not.toHaveBeenCalled();
});
it("先提交地点选择再确认需求，不会自动确认或保存", async () => {
  watchMock
    .mockResolvedValueOnce({
      status: "needs_input",
      conversation: conversation(),
    })
    .mockResolvedValueOnce({
      status: "needs_confirmation",
      conversation: conversation(2, true),
    });
  mount();
  await flush();
  host.querySelector<HTMLInputElement>('input[type="radio"]')!.click();
  await nextTick();
  button("提交补充").click();
  await flush();
  expect(answerMock).toHaveBeenLastCalledWith(
    "r1",
    expect.objectContaining({ choices: { q: "quyuan" }, confirm: false }),
  );
  expect(button("确认需求与地点").disabled).toBe(false);
  expect(confirmMock).not.toHaveBeenCalled();
  button("确认需求与地点").click();
  await flush();
  expect(answerMock).toHaveBeenLastCalledWith(
    "r1",
    expect.objectContaining({
      version: 2,
      confirm: true,
      text: "",
      choices: {},
    }),
  );
  expect(startMock).toHaveBeenCalledTimes(1);
  expect(button("确认并保存 Agent 方案")).toBeTruthy();
});
it("网络结果不确定时重发相同幂等编号，不新增任务", async () => {
  watchMock.mockResolvedValueOnce({
    status: "needs_input",
    conversation: { ...conversation(), phase: "agent", mentions: [] },
  });
  answerMock.mockRejectedValueOnce(new Error("network"));
  mount();
  await flush();
  const text = host.querySelector<HTMLTextAreaElement>("textarea")!;
  text.value = "先看展";
  text.dispatchEvent(new Event("input"));
  await nextTick();
  button("提交补充").click();
  await flush();
  button("提交补充").click();
  await flush();
  expect(answerMock.mock.calls[0]![1].requestId).toBe(
    answerMock.mock.calls[1]![1].requestId,
  );
  expect(startMock).toHaveBeenCalledTimes(1);
});
it("已有对话编号可直接接续，无需满足初始候选表单", async () => {
  input.initialRunId = "persisted-conversation";
  input.candidateIds = [];
  watchMock.mockResolvedValueOnce({
    status: "needs_input",
    conversation: conversation(),
  });
  mount();
  await flush();
  expect(startMock).not.toHaveBeenCalled();
  expect(watchMock.mock.calls[0]![0]).toBe("persisted-conversation");
  expect(host.textContent).toContain("补充回答");
});

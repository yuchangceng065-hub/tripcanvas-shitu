// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApp, h, nextTick, reactive, type App } from "vue";
import { api, confirmTrip } from "./api";
import { createTrip, type Trip } from "./domain";
import TripReview from "./components/TripReview.vue";
vi.mock("./api", () => ({ api: vi.fn(), confirmTrip: vi.fn() }));
const apiMock = vi.mocked(api),
  confirmMock = vi.mocked(confirmTrip);
let app: App,
  host: HTMLDivElement,
  props: { trip: Trip; loggedIn: boolean },
  saved: Trip | undefined;
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
beforeEach(() => {
  vi.resetAllMocks();
  saved = undefined;
  props = reactive({
    trip: createTrip("核验测试", "2026-11-14", 1),
    loggedIn: true,
  });
  props.trip.days[0]!.activities = props.trip.days[0]!.activities.slice(0, 1);
  apiMock.mockImplementation(async (path) =>
    path.endsWith("/preview") ? { issues: [], routes: {} } : {},
  );
  confirmMock.mockImplementation(async (trip) => ({
    trip: {
      ...trip,
      revision: trip.revision + 1,
      validationStatus: "verified",
    },
    revision: trip.revision + 1,
    issues: [],
    idempotent: false,
  }));
  host = document.createElement("div");
  document.body.append(host);
  app = createApp({
    render: () =>
      h(TripReview, { ...props, onConfirmed: (trip: Trip) => (saved = trip) }),
  });
  app.mount(host);
});
afterEach(() => {
  app.unmount();
  host.remove();
});
it("整程预览不自动确认，点击确认才保存已核验版本", async () => {
  await flush();
  expect(host.textContent).toContain("曲院风荷");
  expect(confirmMock).not.toHaveBeenCalled();
  expect(apiMock).toHaveBeenCalledWith("/planner/preview", "POST", {
    trip: expect.objectContaining({ id: props.trip.id }),
  });
  button("确认已核验版本").click();
  await flush();
  expect(confirmMock).toHaveBeenCalledWith(
    expect.objectContaining({ id: props.trip.id }),
    props.trip.revision,
  );
  expect(saved?.validationStatus).toBe("verified");
});
it("有硬冲突时保留草稿并阻止确认", async () => {
  await flush();
  apiMock.mockResolvedValue({
    issues: [{ severity: "error", message: "返程晚到 20 分钟" }],
    routes: {},
  });
  button("重新核验").click();
  await flush();
  expect(host.textContent).toContain("返程晚到 20 分钟");
  expect(button("确认已核验版本").disabled).toBe(true);
  button("确认已核验版本").click();
  await flush();
  expect(confirmMock).not.toHaveBeenCalled();
});
it("修改后丢弃旧预览，迟到的查询结果不能重新启用确认", async () => {
  await flush();
  let resolve!: (value: unknown) => void;
  apiMock.mockImplementation(async (path) =>
    path.endsWith("/preview") ? new Promise((r) => (resolve = r)) : {},
  );
  button("重新核验").click();
  await flush();
  props.trip.revision++;
  await nextTick();
  resolve({ issues: [], routes: {} });
  await flush();
  expect(button("确认已核验版本").disabled).toBe(true);
  expect(host.textContent).toContain("行程已修改");
  expect(confirmMock).not.toHaveBeenCalled();
});
it("确认请求返回前切换行程时，不把旧结果写回当前页面", async () => {
  await flush();
  let release!: (value: {
    trip: Trip;
    revision: number;
    issues: [];
    idempotent: boolean;
  }) => void;
  const pending = new Promise<{
    trip: Trip;
    revision: number;
    issues: [];
    idempotent: boolean;
  }>((r) => {
    release = r;
  });
  confirmMock.mockReturnValue(pending);
  button("确认已核验版本").click();
  await flush();
  const other = createTrip("另一份行程", "2026-11-15", 1);
  other.days[0]!.activities = other.days[0]!.activities.slice(0, 1);
  props.trip = { ...other, validationStatus: "verified" };
  await nextTick();
  release({
    trip: {
      ...props.trip,
      revision: props.trip.revision + 1,
      validationStatus: "verified",
    },
    revision: props.trip.revision + 1,
    issues: [],
    idempotent: false,
  });
  await flush();
  expect(saved).toBeUndefined();
  expect(host.textContent).toContain("核验期间行程已变化");
  expect(confirmMock).toHaveBeenCalledTimes(1);
});
it("核验失败可重试，失败时没有确认写入", async () => {
  await flush();
  apiMock.mockRejectedValueOnce(new Error("道路服务暂不可用"));
  button("重新核验").click();
  await flush();
  expect(host.textContent).toContain("道路服务暂不可用");
  expect(button("确认已核验版本").disabled).toBe(true);
  button("重新核验").click();
  await flush();
  expect(button("确认已核验版本").disabled).toBe(false);
  expect(confirmMock).not.toHaveBeenCalled();
});

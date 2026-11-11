// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApp, nextTick, type App as VueApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { useTrips } from "./store";
import { clone, createTrip, placeById, type Trip } from "./domain";
import { api, confirmTrip } from "./api";
const lab = vi.hoisted(() => ({
  emit: undefined as ((trip: unknown) => void) | undefined,
}));
vi.mock("./api", () => ({
  api: vi.fn(),
  confirmTrip: vi.fn(),
  checkAPI: vi.fn(async () => ({ status: "ok", model: false })),
  connection: { status: "online", model: false, routing: "osrm", map: "osm" },
  backendConfigured: true,
  setToken: vi.fn(),
  remoteSuggestion: vi.fn(),
}));
vi.mock("./components/TripMap.vue", () => ({
  default: { render: () => null },
}));
vi.mock("./components/TripBuilder.vue", () => ({
  default: { render: () => null },
}));
vi.mock("./components/PlanningLab.vue", () => ({
  default: {
    props: ["trip", "loggedIn"],
    setup(
      _props: unknown,
      ctx: { emit: (event: string, ...args: unknown[]) => void },
    ) {
      lab.emit = (trip: unknown) => ctx.emit("open", trip);
      return () => null;
    },
  },
}));
let app: VueApp | undefined, host: HTMLDivElement | undefined;
const flush = async () => {
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
  }
};
const button = (text: string) =>
  [...host!.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  )!;
function oneDay(): Trip {
  const t = createTrip("状态验收", "2026-11-14", 1);
  t.days[0]!.activities = t.days[0]!.activities.slice(0, 1);
  return t;
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  lab.emit = undefined;
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/auth/me") return { username: "test" };
    if (path === "/planner/preview") return { issues: [], routes: {} };
    return {};
  });
});
afterEach(() => {
  app?.unmount();
  host?.remove();
  localStorage.clear();
});
function mount() {
  const pinia = createPinia(),
    store = useTrips(pinia);
  const initial = oneDay();
  store.trips = [initial];
  store.activeId = initial.id;
  host = document.createElement("div");
  document.body.append(host);
  app = createApp(App);
  app.use(pinia).mount(host);
  return { store, initial };
}

it("从已核验原行程打开分支，仍是待核验草稿", async () => {
  const { store, initial } = mount();
  await flush();
  initial.validationStatus = "verified";
  button("行程推演").click();
  await flush();
  expect(lab.emit).toBeTypeOf("function");
  const branch = clone(initial);
  branch.id = "branch-open";
  branch.validationStatus = "verified";
  lab.emit!(branch);
  await flush();
  const opened = store.trips.find((t) => t.id === "branch-open");
  expect(opened?.validationStatus).toBe("draft");
  expect(store.activeId).toBe("branch-open");
});

it("慢确认返回前编辑行程时，旧结果不会覆盖当前页面", async () => {
  const { store, initial } = mount();
  await flush();
  type ConfirmedResult = Awaited<ReturnType<typeof confirmTrip>>;
  let resolve!: (value: ConfirmedResult | PromiseLike<ConfirmedResult>) => void;
  vi.mocked(confirmTrip).mockImplementation(
    () =>
      new Promise<ConfirmedResult>((r) => {
        resolve = r;
      }),
  );
  button("多睡一会，晚点出发").click();
  await flush();
  expect(button("确认顺延并应用")).toBeTruthy();
  expect(button("确认顺延并应用").disabled).toBe(false);
  button("确认顺延并应用").click();
  await flush();
  expect(confirmTrip).toHaveBeenCalledTimes(1);
  const edited = clone(initial);
  edited.days[0]!.activities[0]!.duration = 45;
  store.commit(edited, "等待期间手动编辑", initial.revision);
  await flush();
  resolve({
    trip: { ...clone(initial), revision: 2, validationStatus: "verified" },
    revision: 2,
    issues: [],
    idempotent: false,
  });
  await flush();
  expect(store.trips[0]!.days[0]!.activities[0]!.duration).toBe(45);
  expect(store.trips[0]!.validationStatus).toBe("draft");
  expect(host!.textContent).toContain("行程已变化");
});

it("登录期间本地编辑不会被迟到的云端旧副本覆盖", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/auth/me") throw new Error("未登录");
    if (path === "/trips")
      return new Promise((r) => {
        cloudResolve = r;
      });
    if (path.startsWith("/auth/"))
      return { access_token: "token", username: "tester" };
    return {};
  });
  let cloudResolve!: (value: unknown) => void;
  const { store, initial } = mount();
  await flush();
  const avatar = host!.querySelector<HTMLButtonElement>("button.avatar")!;
  avatar.click();
  await flush();
  const form = host!.querySelector<HTMLFormElement>(".modal form")!;
  const set = (name: string, value: string) => {
    const input = form.querySelector<HTMLInputElement>(
      'input[name="' + name + '"]',
    )!;
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  set("username", "tester");
  set("password", "correct-horse-824");
  await flush();
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/trips")).toBe(
    true,
  );
  const edited = clone(initial);
  edited.days[0]!.activities[0]!.duration = 45;
  store.commit(edited, "登录期间手动编辑", initial.revision);
  await flush();
  cloudResolve([clone(initial)]);
  await flush();
  expect(store.trips[0]!.days[0]!.activities[0]!.duration).toBe(45);
});

it("正常确认未被打断时，已核验版本仍会应用", async () => {
  const { store, initial } = mount();
  await flush();
  vi.mocked(confirmTrip).mockImplementation(async (trip, revision) => ({
    trip: {
      ...clone(trip),
      revision: revision + 1,
      validationStatus: "verified",
    },
    revision: revision + 1,
    issues: [],
    idempotent: false,
  }));
  button("多睡一会，晚点出发").click();
  await flush();
  button("确认顺延并应用").click();
  await flush();
  expect(store.trips[0]!.validationStatus).toBe("verified");
  expect(store.trips[0]!.revision).toBe(2);
});

it("云端确认已提交但本地变化时，明确告知并提供载入云端版本入口", async () => {
  const { store, initial } = mount();
  await flush();
  type ConfirmedResult = Awaited<ReturnType<typeof confirmTrip>>;
  let resolve!: (value: ConfirmedResult | PromiseLike<ConfirmedResult>) => void;
  vi.mocked(confirmTrip).mockImplementation(
    () =>
      new Promise<ConfirmedResult>((r) => {
        resolve = r;
      }),
  );
  button("多睡一会，晚点出发").click();
  await flush();
  button("确认顺延并应用").click();
  await flush();
  const edited = clone(initial);
  edited.days[0]!.activities[0]!.duration = 45;
  store.commit(edited, "等待期间手动编辑", initial.revision);
  await flush();
  const cloud = {
    ...clone(initial),
    revision: 2,
    validationStatus: "verified" as const,
  };
  resolve({ trip: cloud, revision: 2, issues: [], idempotent: false });
  await flush();
  expect(host!.textContent).toContain("云端已保存");
  const load = [...host!.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("载入云端版本"),
  );
  expect(load).toBeTruthy();
  load!.click();
  await flush();
  expect(store.trips[0]!.validationStatus).toBe("verified");
  expect(store.trips[0]!.revision).toBe(2);
  expect(
    store.versions[initial.id]!.some((v) =>
      v.label.includes("替换前的本地版本"),
    ),
  ).toBe(true);
});

it("再次打开同一分支时用云端最新内容刷新本地副本", async () => {
  const { store, initial } = mount();
  await flush();
  initial.validationStatus = "verified";
  button("行程推演").click();
  await flush();
  const first = clone(initial);
  first.id = "branch-refresh";
  first.title = "旧分支内容";
  first.validationStatus = "verified";
  lab.emit!(first);
  await flush();
  expect(store.trips.find((t) => t.id === "branch-refresh")?.title).toBe(
    "旧分支内容",
  );
  const second = clone(first);
  second.title = "云端最新分支内容";
  second.validationStatus = "verified";
  button("行程推演").click();
  await flush(); // reopen the lab: openBranch closes the modal
  lab.emit!(second);
  await flush();
  const opened = store.trips.find((t) => t.id === "branch-refresh");
  expect(opened?.title).toBe("云端最新分支内容");
  expect(opened?.validationStatus).toBe("draft");
});

it("未登录时也能按用户原话查餐厅，选店前保留原行程", async () => {
  const restaurant = { ...placeById("louwailou"), kind: "中餐厅" };
  const cafe = {
    ...restaurant,
    id: "tea-fixture",
    kind: "茶艺馆",
    name: "茶艺馆夹具",
  };
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/auth/me") throw new Error("未登录");
    if (path === "/places/search?q=" + encodeURIComponent("西湖约会餐厅"))
      return { places: [cafe] };
    if (path === "/places/search?q=" + encodeURIComponent("西湖餐厅"))
      return { places: [restaurant] };
    throw new Error("unexpected API " + path);
  });
  const { store, initial } = mount();
  const original = clone(initial);
  await flush();
  const composer = host!.querySelector<HTMLTextAreaElement>(
    'textarea[aria-label="行程调整需求"]',
  )!;
  composer.value = "我想晚上去情侣去的饭店有推荐吗";
  composer.dispatchEvent(new Event("input", { bubbles: true }));
  await nextTick();
  host!
    .querySelector<HTMLButtonElement>('button[aria-label="发送调整需求"]')!
    .click();
  await flush();
  expect(host!.querySelector(".visit-search-card")?.textContent).toContain(
    "餐厅候选",
  );
  expect(host!.querySelector(".visit-search-card")?.textContent).toContain(
    restaurant.name,
  );
  expect(host!.querySelector(".visit-search-card")?.textContent).not.toContain(
    cafe.name,
  );
  expect(store.trips[0]).toEqual(original);
  expect(
    vi
      .mocked(api)
      .mock.calls.every(
        ([path]) => path === "/auth/me" || path.startsWith("/places/search?q="),
      ),
  ).toBe(true);
});

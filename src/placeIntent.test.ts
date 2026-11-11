import { describe, it, expect, vi } from "vitest";
import {
  extractVisitQuery,
  normalizePlaceQuery,
  visitProposal,
} from "./placeIntent";
import {
  createTrip,
  clone,
  registerPlaces,
  lockedChanges,
  schedule,
  proposalErrors,
  type Place,
  type Trip,
} from "./domain";
import { coordinateProposal, coordinationViolations } from "./coordination";
import { routeKey, type RoadRoute } from "./routes";
const campus: Place = {
  id: "osm:way:344311838",
  name: "浙江工业大学（朝晖校区）",
  kind: "地点",
  tag: "搜索确认地点",
  description: "",
  duration: 60,
  cost: 0,
  costKnown: false,
  lng: 120.1617104,
  lat: 30.295896,
  x: 0,
  y: 0,
  indoor: false,
  image: "lake",
  crs: "WGS84",
  coordinateSource: "https://www.openstreetmap.org/way/344311838",
};
registerPlaces([campus]);
function roads(t: Trip, day: number): RoadRoute {
  const ids = t.days[day]!.activities.map((a) => a.placeId);
  return {
    mode: t.mode,
    placeIds: ids,
    source: "osrm",
    legs: ids.slice(1).map(() => ({ distance: 1, minutes: 60 })),
    geometry: {
      type: "LineString",
      coordinates: [
        [120.13, 30.25],
        [120.16, 30.29],
      ],
    },
    waypoints: ids.map(() => ({ location: [120.13, 30.25], distance: 0 })),
    fetchedAt: Date.now(),
    cached: false,
  };
}
describe("想去的地点接入真实路线", () => {
  it("支持常见添加说法与裸地点名称", () => {
    expect(extractVisitQuery("我想添加灵隐寺")).toBe("灵隐寺");
    expect(extractVisitQuery("帮我加入西溪湿地")).toBe("西溪湿地");
    expect(extractVisitQuery("西溪湿地")).toBe("西溪湿地");
  });
  it("识别自然输入和校区简称，保留具体校区", () => {
    expect(extractVisitQuery("我想参观浙工大")).toBe("浙江工业大学");
    expect(extractVisitQuery("想去浙工大看看")).toBe("浙江工业大学");
    expect(extractVisitQuery("我想去看看浙工大")).toBe("浙江工业大学");
    expect(extractVisitQuery("浙工大")).toBe("浙江工业大学");
    expect(extractVisitQuery("我想去浙工大屏峰校区")).toBe(
      "浙江工业大学屏峰校区",
    );
    expect(normalizePlaceQuery(" 浙工大朝晖校区 ")).toBe(
      "浙江工业大学朝晖校区",
    );
  });
  it("室内、轻松和晚出发继续使用对应调整逻辑", () => {
    for (const text of [
      "下雨了，改成室内活动",
      "晚出发一小时",
      "今天轻松一点",
      "我想去室内活动",
      "不要去浙工大",
    ])
      expect(extractVisitQuery(text)).toBeNull();
  });
  it("多地点意图要求逐个确认，不默默忽略后半句", () =>
    expect(() => extractVisitQuery("我想去灵隐寺，然后去浙工大")).toThrow(
      "一次确认一个",
    ));
  it("加入选中地点且保留其他日期和固定返程", () => {
    const t = createTrip(),
      old = clone(t),
      p = visitProposal(t, 1, campus, "浙江工业大学"),
      list = p.trip.days[1]!.activities;
    expect(list).toHaveLength(5);
    expect(list.find((a) => a.placeId === campus.id)).toMatchObject({
      lock: "keep",
      duration: 60,
    });
    expect(list.at(-1)).toEqual(t.days[1]!.activities.at(-1));
    expect(p.trip.days[0]).toEqual(t.days[0]);
    expect(lockedChanges(t, p.trip)).toEqual([]);
    expect(t).toEqual(old);
    expect(p.visit?.placeId).toBe(campus.id);
  });
  it("已存在的地点不重复添加，而是保留后重算", () => {
    const t = createTrip();
    t.days[1]!.activities.splice(2, 0, {
      id: "campus-existing",
      placeId: campus.id,
      duration: 45,
      lock: "none",
    });
    const p = visitProposal(t, 1, campus, "浙工大");
    expect(
      p.trip.days[1]!.activities.filter((a) => a.placeId === campus.id),
    ).toHaveLength(1);
    expect(
      p.trip.days[1]!.activities.find((a) => a.id === "campus-existing"),
    ).toMatchObject({ lock: "keep", duration: 45 });
  });
  it("指定起点不被新地点挤到后面", () => {
    const t = createTrip();
    t.requirements = {
      city: "杭州",
      startDate: "2026-11-07",
      days: 2,
      people: 2,
      mode: "walk",
      start: 570,
      end: 1110,
      pace: "balanced",
      interests: [],
      originPlaceId: "quyuan",
    };
    const p = visitProposal(t, 0, campus, "浙工大");
    expect(p.trip.days[0]!.activities[0]!.placeId).toBe("quyuan");
  });
  it("冲突协调保留刚选中的目的地及完整停留", async () => {
    const t = createTrip(),
      p = visitProposal(t, 1, campus, "浙工大"),
      r = roads(p.trip, 1),
      map = { [routeKey(p.trip, 1)]: r };
    const result = await coordinateProposal(t, p, 1, map, vi.fn());
    expect(result.coordination?.resolved).toBe(true);
    expect(
      result.trip.days[1]!.activities.find((a) => a.placeId === campus.id),
    ).toMatchObject({ lock: "keep", duration: 60 });
    expect(result.visit).toEqual(p.visit);
    expect(proposalErrors(t, result.trip, map, 1)).toEqual([]);
    expect(schedule(result.trip, 1, r).at(-1)!.earliest).toBe(1020);
    expect(coordinationViolations(t, result, map)).toEqual([]);
  });
  it("未注册地点和超过单日上限明确拒绝", () => {
    const t = createTrip();
    expect(() =>
      visitProposal(t, 1, { ...campus, id: "invented" }, "未知"),
    ).toThrow("真实地点");
    while (t.days[1]!.activities.length < 12)
      t.days[1]!.activities.unshift({
        id: crypto.randomUUID(),
        placeId: "museum",
        duration: 30,
        lock: "none",
      });
    expect(() => visitProposal(t, 1, campus, "浙工大")).toThrow("12 个地点");
  });
});

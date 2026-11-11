import { describe, it, expect } from "vitest";
import {
  createTrip,
  clone,
  schedule,
  validate,
  departureDelay,
  departureProposal,
  validDepartureProposal,
  proposalErrors,
  lockedChanges,
} from "./domain";
import { routeKey, type RoadRoute } from "./routes";
function roads(
  t: ReturnType<typeof createTrip>,
  day = 0,
  minutes = [26, 7, 15, 28],
) {
  const ids = t.days[day]!.activities.map((a) => a.placeId);
  return {
    [routeKey(t, day)]: {
      mode: t.mode,
      placeIds: ids,
      source: "osrm",
      legs: ids
        .slice(1)
        .map((_, i) => ({ minutes: minutes[i] ?? 10, distance: 1 })),
      geometry: {
        type: "LineString",
        coordinates: [
          [120.13, 30.25],
          [120.15, 30.26],
        ],
      },
      waypoints: ids.map(() => ({ location: [120.13, 30.25], distance: 0 })),
      fetchedAt: Date.now(),
      cached: false,
    } as RoadRoute,
  };
}
describe("整日顺延", () => {
  it("按分钟或小时识别简单出发修改，混合需求留给助手", () => {
    expect(departureDelay("晚出发一小时")).toBe(60);
    expect(departureDelay("出发推迟30分钟")).toBe(30);
    expect(departureDelay("多睡半小时")).toBe(30);
    expect(departureDelay("晚出发一个半小时")).toBe(90);
    expect(departureDelay("推迟1.5小时出发")).toBe(90);
    expect(departureDelay("晚出发十五分钟")).toBe(15);
    expect(departureDelay("不要晚出发")).toBeNull();
    expect(departureDelay("晚出发两小时")).toBe(120);
    expect(departureDelay("晚出发一小时，改成室内")).toBeNull();
  });
  it("普通活动整体后移，保留地点、顺序、停留时长和其他日期", () => {
    const t = createTrip();
    t.days[0]!.activities[2]!.lock = "keep";
    const r = roads(t),
      old = clone(t);
    const p = departureProposal(t, 0, 60, r),
      a = schedule(t, 0, r[routeKey(t, 0)]),
      b = schedule(p.trip, 0, r[routeKey(t, 0)]);
    expect(b.map((e, i) => e.start - a[i]!.start)).toEqual([
      60, 60, 60, 60, 60,
    ]);
    expect(p.trip.days[0]!.activities).toEqual(t.days[0]!.activities);
    expect(p.trip.days[1]).toEqual(t.days[1]);
    expect(t).toEqual(old);
    expect(lockedChanges(t, p.trip)).toEqual([]);
  });
  it("明确确认后才顺延固定午餐，并按道路到达时间消除已有冲突", () => {
    const t = createTrip(),
      r = roads(t),
      p = departureProposal(t, 0, 60, r);
    expect(p.trip.days[0]!.activities[2]!.fixedStart).toBe(780);
    expect(
      proposalErrors(t, p.trip, r).some((i) =>
        i.message.includes("晚 63 分钟"),
      ),
    ).toBe(true);
    const shifted = departureProposal(t, 0, 60, r, {
      shiftFixed: true,
      extendEnd: false,
    });
    expect(shifted.trip.days[0]!.activities[2]!.fixedStart).toBe(843);
    expect(shifted.trip.days[0]!.activities[2]!.lock).toBe("fixed");
    expect(proposalErrors(t, shifted.trip, r)).toEqual([]);
    expect(validDepartureProposal(t, shifted, r)).toBe(true);
    expect(shifted.changes.some((c) => c.includes("13:00 → 14:03"))).toBe(true);
  });
  it("延长当天结束需要单独选择，不修改其他日期", () => {
    const t = createTrip(),
      r = roads(t),
      p = departureProposal(t, 0, 240, r, {
        shiftFixed: true,
        extendEnd: false,
      });
    expect(
      proposalErrors(t, p.trip, r).some((i) => i.message.includes("超过当天")),
    ).toBe(true);
    const extended = departureProposal(t, 0, 240, r, {
      shiftFixed: true,
      extendEnd: true,
    });
    expect(extended.trip.days[0]!.end).toBe(1276);
    expect(proposalErrors(t, extended.trip, r)).toEqual([]);
    expect(extended.trip.days[1]).toEqual(t.days[1]);
  });
  it("返程固定时间和需求期限不能被顺延选项推后", () => {
    const t = createTrip(),
      r = roads(t, 1, [15, 12, 180]);
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
      destinationPlaceId: "station",
      returnBy: 1020,
    };
    const p = departureProposal(t, 1, 60, r, {
      shiftFixed: true,
      extendEnd: true,
    });
    expect(p.trip.days[1]!.activities.at(-1)!.fixedStart).toBe(1020);
    expect(p.trip.requirements!.returnBy).toBe(1020);
    expect(
      proposalErrors(t, p.trip, r).some((i) => i.id === "return-constraint"),
    ).toBe(true);
  });
  it("其他日期的旧冲突不会阻止当前日期的有效调整", () => {
    const t = createTrip(),
      r = roads(t);
    t.days[1]!.start = 1020;
    const p = departureProposal(t, 0, 60, r, {
      shiftFixed: true,
      extendEnd: false,
    });
    expect(
      validate(p.trip, r).some((i) => i.day === 1 && i.severity === "error"),
    ).toBe(true);
    expect(proposalErrors(t, p.trip, r)).toEqual([]);
  });
  it("应用前拒绝篡改活动、其他日期或过期版本", () => {
    const t = createTrip(),
      r = roads(t),
      p = departureProposal(t, 0, 60, r, {
        shiftFixed: true,
        extendEnd: false,
      });
    p.trip.days[0]!.activities[0]!.duration = 15;
    expect(validDepartureProposal(t, p, r)).toBe(false);
    const other = departureProposal(t, 0, 60, r);
    other.trip.days[1]!.start++;
    expect(validDepartureProposal(t, other, r)).toBe(false);
    const stale = departureProposal(t, 0, 60, r);
    t.revision++;
    expect(validDepartureProposal(t, stale, r)).toBe(false);
  });
  it("固定安排迟到后，后续时间继续向前推进，不跳回过去", () => {
    const t = createTrip(),
      r = roads(t, 0, [70, 70, 70, 70]),
      entries = schedule(t, 0, r[routeKey(t, 0)]);
    expect(entries[2]!.start).toBe(890);
    expect(
      entries.every(
        (e, i) => i === 0 || e.start >= entries[i - 1]!.end + e.travel,
      ),
    ).toBe(true);
  });
  it("不能跨越午夜或制造空行程的非法每日范围", () => {
    const t = createTrip();
    t.days[0]!.start = 1400;
    expect(() => departureProposal(t, 0, 60)).toThrow("超出当天");
    const empty = createTrip();
    empty.days[0]!.activities = [];
    const p = departureProposal(empty, 0, 600);
    expect(proposalErrors(empty, p.trip).length).toBeGreaterThan(0);
    const fixed = departureProposal(
      empty,
      0,
      600,
      {},
      { shiftFixed: false, extendEnd: true },
    );
    expect(proposalErrors(empty, fixed.trip)).toEqual([]);
  });
});

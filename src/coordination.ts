import {
  clone,
  departureProposal,
  lockedChanges,
  placeById,
  validate,
  returnAnchor,
  route,
  schedule,
  time,
  validDepartureProposal,
  type Activity,
  type Proposal,
  type Trip,
} from "./domain";
import {
  matchesRoute,
  routeKey,
  type RoadRoute,
  type RouteMap,
} from "./routes";

export type RouteLoader = (trip: Trip, day: number) => Promise<RoadRoute>;
export function protectedActivity(t: Trip, day: number, a: Activity) {
  return (
    a.lock !== "none" ||
    returnAnchor(t, day, a) ||
    (day === 0 && a.placeId === t.requirements?.originPlaceId) ||
    (day === t.days.length - 1 &&
      a.placeId === t.requirements?.destinationPlaceId)
  );
}
const minimum = (a: Activity) => Math.min(a.duration, 30);
function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object")
    return (
      "{" +
      Object.entries(value)
        .filter(([, v]) => v !== undefined)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => JSON.stringify(k) + ":" + canonical(v))
        .join(",") +
      "}"
    );
  return JSON.stringify(value);
}
function sameTrip(a: Trip, b: Trip) {
  const normalize = (t: Trip) => {
    const copy = clone(t);
    delete copy.placeSnapshots;
    if (copy.mobility == null) delete copy.mobility;
    if (copy.requirements == null) delete copy.requirements;
    else
      for (const key of [
        "originPlaceId",
        "destinationPlaceId",
        "returnBy",
      ] as const)
        if (copy.requirements[key] == null) delete copy.requirements[key];
    for (const day of copy.days)
      for (const activity of day.activities)
        if (activity.fixedStart == null) delete activity.fixedStart;
    return copy;
  };
  return canonical(normalize(a)) === canonical(normalize(b));
}
interface Deadline {
  index: number;
  late: number;
  includeSelf: boolean;
}
function deadlines(t: Trip, day: number, routes: RouteMap): Deadline[] {
  const entries = schedule(t, day, routes[routeKey(t, day)]),
    result: Deadline[] = [];
  entries.forEach((e, index) => {
    let deadline =
      e.activity.lock === "fixed" ? e.activity.fixedStart : undefined;
    if (
      day === t.days.length - 1 &&
      e.place.id === t.requirements?.destinationPlaceId &&
      t.requirements.returnBy != null
    )
      deadline = Math.min(deadline ?? Infinity, t.requirements.returnBy);
    if (deadline !== undefined && e.earliest > deadline)
      result.push({ index, late: e.earliest - deadline, includeSelf: false });
  });
  const last = entries.at(-1);
  if (last && last.end > t.days[day]!.end)
    result.push({
      index: entries.length - 1,
      late: last.end - t.days[day]!.end,
      includeSelf: true,
    });
  return result;
}
function ready(t: Trip, day: number, routes: RouteMap) {
  return (
    t.days[day]!.activities.length < 2 ||
    matchesRoute(t, day, routes[routeKey(t, day)])
  );
}
function errors(t: Trip, day: number, routes: RouteMap) {
  return validate(t, routes).filter(
    (issue) =>
      issue.severity === "error" &&
      (issue.day === undefined || issue.day === day),
  );
}
function lateness(t: Trip, day: number, routes: RouteMap) {
  return deadlines(t, day, routes).reduce((sum, d) => sum + d.late, 0);
}

// Preserve the requested places and order first. Spend only the slack of unlocked visits.
export function fitDurations(input: Trip, day: number, routes: RouteMap): Trip {
  if (!ready(input, day, routes)) throw new Error("自动协调需要先核验道路路线");
  const t = clone(input),
    activities = t.days[day]!.activities;
  const floors = new Map(activities.map((a) => [a.id, minimum(a)]));
  for (let step = 0; step < activities.length * activities.length + 1; step++) {
    let adjusted = false;
    for (const deadline of deadlines(t, day, routes)) {
      const last = deadline.includeSelf ? deadline.index : deadline.index - 1;
      for (let i = last; i >= 0; i--) {
        const a = activities[i]!,
          slack = a.duration - floors.get(a.id)!;
        if (a.lock === "fixed") break;
        if (protectedActivity(t, day, a) || slack <= 0) continue;
        a.duration -= Math.min(slack, deadline.late);
        adjusted = true;
        break;
      }
      if (adjusted) break;
    }
    if (!adjusted) break;
  }
  return t;
}
function estimatedTravel(t: Trip, day: number) {
  const a = t.days[day]!.activities;
  return a
    .slice(1)
    .reduce(
      (sum, p, i) =>
        sum +
        route(placeById(a[i]!.placeId), placeById(p.placeId), t.mode).minutes,
      0,
    );
}
function shorterOrder(input: Trip, day: number): Trip | null {
  const a = input.days[day]!.activities,
    segments: number[] = [];
  let group = 0;
  for (const p of a) {
    if (protectedActivity(input, day, p)) {
      segments.push(-1);
      group++;
    } else segments.push(group);
  }
  let best: Trip | null = null,
    score = estimatedTravel(input, day);
  for (let i = 0; i < a.length; i++)
    for (let j = i + 1; j < a.length; j++) {
      if (segments[i] === -1 || segments[i] !== segments[j]) continue;
      const trial = clone(input),
        list = trial.days[day]!.activities;
      [list[i], list[j]] = [list[j]!, list[i]!];
      const next = estimatedTravel(trial, day);
      if (next < score - 5) {
        best = trial;
        score = next;
      }
    }
  return best;
}
function removable(t: Trip, day: number, routes: RouteMap): number | null {
  const a = t.days[day]!.activities;
  if (a.filter((p) => !returnAnchor(t, day, p)).length <= 1) return null;
  const first = deadlines(t, day, routes)[0];
  if (!first) return null;
  const last = first.includeSelf ? first.index : first.index - 1;
  let best: number | null = null,
    score = -Infinity;
  for (let i = 0; i <= last; i++) {
    const p = a[i]!;
    if (protectedActivity(t, day, p)) continue;
    const left = a[i - 1],
      right = a[i + 1];
    const saving =
      p.duration +
      (left
        ? route(placeById(left.placeId), placeById(p.placeId), t.mode).minutes
        : 0) +
      (right
        ? route(placeById(p.placeId), placeById(right.placeId), t.mode).minutes
        : 0) -
      (left && right
        ? route(placeById(left.placeId), placeById(right.placeId), t.mode)
            .minutes
        : 0);
    if (saving > score) {
      best = i;
      score = saving;
    }
  }
  return best;
}
function differences(base: Trip, t: Trip, day: number) {
  const before = base.days[day]!.activities,
    after = t.days[day]!.activities,
    changes: string[] = [];
  for (const a of before) {
    const b = after.find((x) => x.id === a.id),
      name = placeById(a.placeId).name;
    if (!b)
      changes.push(
        `为满足固定时间，移除未锁定的 ${name}（原停留 ${a.duration} 分钟）`,
      );
    else if (a.duration !== b.duration)
      changes.push(
        `${name} 停留 ${a.duration} → ${b.duration} 分钟，为固定安排预留时间`,
      );
  }
  const retained = before
    .filter((a) => after.some((b) => a.id === b.id))
    .map((a) => a.id);
  if (retained.join(",") !== after.map((a) => a.id).join(","))
    changes.unshift("重排未锁定活动顺序，减少往返路程");
  return changes;
}
function latestEarlierStart(
  t: Trip,
  day: number,
  routes: RouteMap,
): number | undefined {
  const test = clone(t),
    limit = t.days[day]!.start;
  test.days[day]!.start = 0;
  if (errors(test, day, routes).length || limit <= 0) return;
  let low = 0,
    high = limit - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    test.days[day]!.start = mid;
    if (errors(test, day, routes).length) high = mid - 1;
    else low = mid;
  }
  return low < limit ? low : undefined;
}

function shorterDelayStart(
  before: Trip,
  departure: NonNullable<Proposal["departure"]>,
  routes: RouteMap,
): number | undefined {
  const { day, delay, shiftFixed, extendEnd } = departure;
  const feasible = (minutes: number) => {
    const trial = departureProposal(before, day, minutes, routes, { shiftFixed, extendEnd });
    return !errors(fitDurations(trial.trip, day, routes), day, routes).length;
  };
  if (delay <= 1 || !feasible(1)) return;
  let low = 1,
    high = delay - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (feasible(mid)) low = mid;
    else high = mid - 1;
  }
  return before.days[day]!.start + low;
}
export async function coordinateProposal(
  before: Trip,
  input: Proposal,
  day: number,
  routes: RouteMap,
  load: RouteLoader,
  onStage?: (text: string) => void,
): Promise<Proposal> {
  const basis = clone(input.trip),
    warnings: string[] = [];
  const ensure = async (t: Trip) => {
    if (t.days[day]!.activities.length > 1 && !ready(t, day, routes)) {
      onStage?.("正在核验调整后的道路路线…");
      const r = await load(t, day);
      if (!matchesRoute(t, day, r)) throw new Error("协调方案的道路路线不完整");
      routes[routeKey(t, day)] = r;
    }
  };
  await ensure(basis);
  if (!errors(basis, day, routes).length) return input;
  onStage?.("正在协调停留时长与固定安排…");
  let best = fitDurations(basis, day, routes);
  if (errors(best, day, routes).length) {
    const ordered = shorterOrder(basis, day);
    if (ordered) {
      try {
        await ensure(ordered);
        const fitted = fitDurations(ordered, day, routes);
        if (lateness(fitted, day, routes) < lateness(best, day, routes))
          best = fitted;
      } catch {
        warnings.push("备选顺序的道路核验失败，已保留核验过的顺序");
      }
    }
  }
  // A bounded search avoids hammering the shared public road service.
  for (
    let attempt = 0;
    attempt < 3 && errors(best, day, routes).length;
    attempt++
  ) {
    const index = removable(best, day, routes);
    if (index === null) break;
    const trial = clone(best);
    trial.days[day]!.activities.splice(index, 1);
    try {
      await ensure(trial);
      const fitted = fitDurations(trial, day, routes);
      if (lateness(fitted, day, routes) >= lateness(best, day, routes)) break;
      best = fitted;
    } catch {
      warnings.push("删减方案的道路核验失败，未采用该方案");
      break;
    }
  }
  const resolved = errors(best, day, routes).length === 0,
    changes = differences(basis, best, day);
  const result: Proposal = {
    ...input,
    trip: best,
    changes: [...input.changes, ...changes],
    coordination: {
      day,
      basis,
      changes,
      resolved,
      warnings,
      ...(!resolved
        ? {
            earlierStart: input.departure
              ? shorterDelayStart(before, input.departure, routes)
              : latestEarlierStart(basis, day, routes),
          }
        : {}),
    },
  };
  // Never turn an unsafe model output into a trusted coordinated plan.
  const violations = coordinationViolations(before, result, routes);
  if (violations.length) throw new Error(violations.join("；"));
  return result;
}

export function coordinationViolations(
  before: Trip,
  p: Proposal,
  routes: RouteMap,
): string[] {
  const c = p.coordination;
  if (!c) return lockedChanges(before, p.trip);
  const { day, basis } = c;
  if (!p.trip.days[day] || !ready(p.trip, day, routes))
    return ["协调方案的道路路线尚未核验"];
  if (
    p.baseRevision !== before.revision ||
    !basis.days[day] ||
    !p.trip.days[day]
  )
    return ["方案已过期，请重新生成"];
  if (p.departure) {
    if (
      !validDepartureProposal(
        before,
        { ...p, trip: basis, coordination: undefined },
        routes,
      )
    )
      return ["顺延的授权范围已变化，请重新生成"];
  } else {
    const allowed = clone(before);
    allowed.days[day] = clone(basis.days[day]!);
    if (
      !sameTrip(allowed, basis) ||
      basis.days[day]!.date !== before.days[day]!.date ||
      basis.days[day]!.end !== before.days[day]!.end
    )
      return ["调整超出当前日期的范围"];
    const locked = lockedChanges(before, basis);
    if (locked.length) return locked;
  }
  const expected = clone(basis);
  expected.days[day]!.activities = clone(p.trip.days[day]!.activities);
  if (!sameTrip(expected, p.trip))
    return ["自动协调只能调整本日活动，不能修改其他条件"];
  const old = basis.days[day]!.activities,
    list = p.trip.days[day]!.activities;
  if (new Set(list.map((a) => a.id)).size !== list.length)
    return ["活动 ID 重复"];
  for (const a of old) {
    const b = list.find((x) => x.id === a.id);
    if (
      protectedActivity(basis, day, a) &&
      (!b || canonical(a) !== canonical(b))
    )
      return [`${placeById(a.placeId).name} 的锁定或起终点安排必须保留`];
  }
  for (const b of list) {
    const a = old.find((x) => x.id === b.id);
    if (
      !a ||
      b.placeId !== a.placeId ||
      b.lock !== a.lock ||
      b.fixedStart !== a.fixedStart ||
      b.duration < minimum(a) ||
      b.duration > a.duration
    )
      return ["协调方案包含未经允许的活动改动"];
  }
  const segment = (list: Activity[], id: string) => {
    let group = 0;
    for (const a of list) {
      if (a.id === id) return group;
      if (protectedActivity(basis, day, a)) group++;
    }
    return -1;
  };
  for (const b of list)
    if (segment(old, b.id) !== segment(list, b.id))
      return ["不能跨越锁定安排重排活动"];
  if (
    old.some((a) => !returnAnchor(basis, day, a)) &&
    !list.some((a) => !returnAnchor(basis, day, a))
  )
    return ["至少保留一个游览地点"];
  return [];
}
export function earlierProposal(p: Proposal): Proposal {
  const c = p.coordination;
  if (!c || c.earlierStart === undefined || p.departure)
    throw new Error("没有可用的提前出发方案");
  const trip = clone(c.basis),
    from = trip.days[c.day]!.start;
  trip.days[c.day]!.start = c.earlierStart;
  const change = `为保留完整停留时间，出发 ${time(from)} → ${time(c.earlierStart)}`;
  return {
    ...p,
    trip,
    departure: undefined,
    source: "schedule",
    changes: [...p.changes.filter((x) => !c.changes.includes(x)), change],
    coordination: {
      ...c,
      basis: clone(trip),
      changes: [change],
      resolved: true,
      earlierStart: undefined,
    },
  };
}

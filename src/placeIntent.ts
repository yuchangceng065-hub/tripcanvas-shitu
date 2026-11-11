import {
  clone,
  lockedChanges,
  places,
  placeById,
  returnAnchor,
  schedule,
  uid,
  type Place,
  type Proposal,
  type Trip,
} from "./domain";
export function normalizePlaceQuery(query: string) {
  return query.trim().replace(/^浙工大/, "浙江工业大学");
}

export function rankVisitPlaces(query: string, matches: Place[]) {
  if (query !== "浙江工业大学") return matches;
  const campus = /^浙江工业大学(?:[（(][^（）()]+校区[）)]|[^（）()]+校区)$/;
  return [
    ...matches.filter((place) => campus.test(place.name)),
    ...matches.filter((place) => !campus.test(place.name)),
  ];
}
const mealWords = /餐厅|饭店|餐馆|酒楼|晚餐|晚饭|吃饭|吃什么|美食/;
export const dinnerStart = 17 * 60 + 30;

export function diningSearch(text: string, trip: Trip, day: number) {
  if (
    !mealWords.test(text) ||
    !/推荐|哪里|哪儿|哪家|什么|找|适合|情侣|约会/.test(text) ||
    /(?:不想|不要|取消|删除)(?:再)?(?:去|找|加|安排|吃)?(?:餐厅|饭店|餐馆|晚餐|晚饭)/.test(
      text,
    )
  )
    return null;
  const explicitArea = text.match(
    /西湖区|西湖|湖滨|灵隐|满觉陇|钱江新城|西溪|武林|运河|龙井|上城区|拱墅区|滨江区|余杭区|萧山区/,
  )?.[0];
  const lastStop = trip.days[day]?.activities.findLast(
    (activity) => !returnAnchor(trip, day, activity),
  );
  const place = lastStop && placeById(lastStop.placeId);
  const area =
    explicitArea ??
    place?.address?.match(
      /西湖区|上城区|拱墅区|滨江区|余杭区|萧山区|临平区|钱塘区|富阳区|临安区/,
    )?.[0] ??
    (place &&
    place.lat >= 30.19 &&
    place.lat <= 30.29 &&
    place.lng >= 120.08 &&
    place.lng <= 120.18
      ? "西湖"
      : "杭州");
  const dateNight = /情侣|约会|纪念日|浪漫/.test(text);
  return {
    query: area + (dateNight ? "约会餐厅" : "餐厅"),
    fallback: area + "餐厅",
    dinner: /晚上|晚餐|晚饭|夜宵|傍晚/.test(text),
  };
}

export function isDiningPlace(place: Place) {
  return (
    /餐|饭店|酒楼|火锅|烧烤|料理|小吃|面馆/.test(place.kind) &&
    !/咖啡|茶艺|甜品|快餐/.test(place.kind)
  );
}

export function extractVisitQuery(text: string): string | null {
  const match = text
    .trim()
    .match(
      /^(?:我(?:们)?(?:想|要|打算|计划)?(?:去|到|参观|游览|逛|添加|加入|加上|加)|想(?:去|到|参观|游览|逛|添加|加入|加上|加)|请(?:帮我)?(?:安排|加入|添加|新增)|(?:帮我|带我)(?:去|参观|添加|加入|加上|加)|(?:加入|添加|新增)(?:一站)?|去|参观|游览)\s*(.+)$/,
    );
  const bare = text.trim();
  if (
    !match &&
    ((!/^[^，。！？!?；;]{0,50}(?:大学|学院|校区|寺|塔|公园|博物馆|美术馆|车站|浙工大|湿地|景区|古镇|湖|山|岛|故居|广场|码头|咖啡馆)$/.test(
      bare,
    ) &&
      !places.some((p) => p.name === bare)) ||
      /不要|不想|删除|取消/.test(bare))
  )
    return null;
  let query = (match?.[1] ?? bare)
    .trim()
    .replace(/^(?:一下|一趟|一站|看看|逛逛)\s*/, "")
    .replace(/[。！？!?]+$/, "")
    .replace(/(?:看看|逛逛|参观(?:一下)?|游览(?:一下)?|玩一下|玩一会儿?)$/, "")
    .trim();
  if (/^(?:室内|轻松|休息|晚点|睡觉|晚出发)/.test(query)) return null;
  if (/[,，;；]|然后|再去|并且|和.*(?:大学|寺|馆|校区|公园)/.test(query))
    throw new Error(
      "请一次确认一个地点，例如“我想参观浙工大”，确认后可继续添加。",
    );
  if (query.length < 2 || query.length > 80)
    throw new Error("请输入 2—80 个字的具体地点或校区名称");
  query = normalizePlaceQuery(query);
  return query;
}
function score(t: Trip, day: number) {
  const entries = schedule(t, day),
    d = t.days[day]!;
  let late = entries.reduce(
    (sum, e) =>
      sum +
      (e.activity.lock === "fixed" && e.activity.fixedStart !== undefined
        ? Math.max(0, e.earliest - e.activity.fixedStart)
        : 0),
    0,
  );
  late += Math.max(0, (entries.at(-1)?.end ?? d.start) - d.end);
  const r = t.requirements;
  if (day === t.days.length - 1 && r?.returnBy != null) {
    const dest = entries.find((e) => e.place.id === r.destinationPlaceId);
    if (dest) late += Math.max(0, dest.earliest - r.returnBy);
  }
  return late * 10000 + entries.reduce((sum, e) => sum + e.travel, 0);
}
export function visitProposal(
  before: Trip,
  day: number,
  place: Place,
  query: string,
  dinner = false,
): Proposal {
  const known = placeById(place.id);
  if (!known || !before.days[day]) throw new Error("请先搜索并确认真实地点");
  const initial = before.days[day]!,
    existing = initial.activities.find((a) => a.placeId === place.id);
  let next = clone(before),
    changes: string[] = [];
  if (existing) {
    const a = next.days[day]!.activities.find((a) => a.id === existing.id)!;
    if (a.lock === "none") a.lock = "keep";
    changes.push(`${known.name} 已在当天行程，作为必须保留地点重新核验路线`);
  } else {
    if (initial.activities.length >= 12)
      throw new Error("当天已达 12 个地点，请先移除一个不需要的地点");
    const activity = {
      id: uid(),
      placeId: known.id,
      duration: Math.min(180, Math.max(30, known.duration)),
      lock: "keep" as const,
    };
    const lower =
      day === 0 &&
      initial.activities[0]?.placeId === before.requirements?.originPlaceId
        ? 1
        : 0;
    const anchor = initial.activities.findIndex(
      (a) =>
        returnAnchor(before, day, a) ||
        (day === before.days.length - 1 &&
          a.placeId === before.requirements?.destinationPlaceId),
    );
    const upper = anchor < 0 ? initial.activities.length : anchor;
    let best = Infinity;
    for (let at = lower; at <= upper; at++) {
      const trial = clone(before);
      trial.days[day]!.activities.splice(at, 0, activity);
      const row =
        dinner &&
        schedule(trial, day).find((entry) => entry.activity.id === activity.id);
      const cost =
        score(trial, day) + (row && row.start < dinnerStart ? 1440 : 0);
      if (cost < best) {
        best = cost;
        next = trial;
      }
    }
    if (best === Infinity)
      throw new Error("起终点条件下没有可插入的位置，请先确认当天安排");
    changes.push(
      `加入 ${known.name}，停留 ${activity.duration} 分钟，作为必须保留地点`,
    );
  }
  const violations = lockedChanges(before, next);
  if (violations.length) throw new Error(violations.join("；"));
  changes.push("按地理位置选择插入位置，并以真实道路核验当天安排");
  return {
    id: uid(),
    baseRevision: before.revision,
    trip: next,
    summary: `已为 ${known.name} 生成 Day ${day + 1} 路线预览，确认后才保存。`,
    changes,
    source: "schedule",
    visit: { day, placeId: known.id, query },
  };
}

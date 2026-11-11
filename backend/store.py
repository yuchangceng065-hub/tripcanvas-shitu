"""Persistence helpers, trip diff checks and place catalog writes."""
from __future__ import annotations
import hashlib, json, math, time, uuid

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.amap import restricted_parking
from backend.db import PlaceRecord, SessionLocal, TripRow, VersionRow
from backend.places import POIS
from backend.routing import RouteBody
from backend.schemas import Trip


def snapshot(t: Trip):
    return t.model_dump(mode='json', exclude_none=True)


def proposal_key(trip: Trip):
    data = trip.model_dump(mode='json', exclude_none=True,
                           exclude={'revision', 'validationStatus', 'placeSnapshots'})
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def travel(a, b, mode):
    rad = math.pi / 180
    h = math.sin((b['lat'] - a['lat']) * rad / 2) ** 2 + math.cos(a['lat'] * rad) * math.cos(b['lat'] * rad) * math.sin((b['lng'] - a['lng']) * rad / 2) ** 2
    distance = 6371 * 2 * math.asin(math.sqrt(h)) * 1.25
    return max(5, math.floor(distance / (4.5 if mode == 'walk' else 23) * 60 + (8 if mode == 'drive' else 0) + .5))


def issues(t: Trip, routes=None):
    result = []
    arrivals = {}
    for i, d in enumerate(t.days):
        end = d.start
        for j, a in enumerate(d.activities):
            minutes = (routes[i]['legs'][j - 1]['minutes'] if routes and i in routes else travel(POIS[d.activities[j - 1].placeId], POIS[a.placeId], t.mode)) if j else 0
            earliest = end + minutes
            arrivals[(i, a.placeId)] = earliest
            fixed = a.fixedStart if a.lock == 'fixed' else None
            start = max(earliest, fixed if fixed is not None else earliest)
            end = start + a.duration
            if (fixed is not None and earliest > fixed) or end > d.end:
                result.append({'id': a.id, 'message': f'第 {i + 1} 天 {POIS[a.placeId]["name"]} 存在时间冲突', 'severity': 'error'})
    r = t.requirements
    if r and r.returnBy is not None:
        arrived = arrivals.get((len(t.days) - 1, r.destinationPlaceId))
        if arrived is None or arrived > r.returnBy:
            result.append({'id': 'return-constraint', 'message': '末日返程地点缺失或无法在最晚到达时间前抵达', 'severity': 'error'})
    return result


def verify_proposal(before: Trip, after: Trip, day: int):
    if before.id != after.id or before.city != after.city or before.people != after.people or before.mode != after.mode or before.revision != after.revision or len(before.days) != len(after.days):
        raise ValueError('修改超出了授权范围')
    if before.title != after.title:
        raise ValueError('不能修改旅行名称')
    if before.requirements != after.requirements:
        raise ValueError('不能修改需求卡片')
    if before.mobility != after.mobility:
        raise ValueError('不能修改交通和停车条件')
    for i, d in enumerate(before.days):
        n = after.days[i]
        if i != day and d.model_dump() != n.model_dump():
            raise ValueError('不能修改其他日期')
        if d.date != n.date or d.end != n.end:
            raise ValueError('不能修改日期和结束约束')
        for a in d.activities:
            b = next((b for d2 in after.days for b in d2.activities if b.id == a.id), None)
            if a.lock != 'none' and (not b or a.placeId != b.placeId or a.lock != b.lock):
                raise ValueError('锁定地点不能移除或替换')
            if a.lock == 'fixed' and (a.model_dump() != b.model_dump() or not any(x.id == a.id for x in n.activities)):
                raise ValueError('固定安排不能修改')
    return after


def owned(s: Session, id, user):
    t = s.get(TripRow, id)
    if not t or t.owner != user.id:
        raise HTTPException(404, '旅行不存在')
    return t


def persist_trip(s: Session, user, trip: Trip, *, commit=True, base_revision=None):
    """Idempotent write used by both manual sync and the confirm transaction."""
    data = snapshot(trip)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
    row = s.get(TripRow, trip.id, populate_existing=True)
    if row is None:
        if base_revision not in (None, 0):
            raise HTTPException(409, '草案基准版本已失效，请重新生成')
        insert = postgres_insert if s.get_bind().dialect.name == 'postgresql' else sqlite_insert
        created = s.execute(
            insert(TripRow).values(id=trip.id, owner=user.id, revision=trip.revision, snapshot=raw)
            .on_conflict_do_nothing(index_elements=[TripRow.id]).returning(TripRow.id)
        ).scalar_one_or_none()
        if created is None:
            row = s.get(TripRow, trip.id, populate_existing=True)
            if row is None:
                raise HTTPException(409, '旅行已被其他请求修改，请重新打开')
    if row:
        if row.owner != user.id:
            raise HTTPException(404, '旅行不存在')
        if row.revision > trip.revision:
            raise HTTPException(409, '云端存在更新版本，请重新打开旅行')
        if row.revision == trip.revision:
            if snapshot(Trip.model_validate_json(row.snapshot)) != data:
                raise HTTPException(409, '同一版本不能覆盖，请更新版本号')
            return trip.revision, False
        if base_revision is not None and row.revision != base_revision:
            raise HTTPException(409, '旅行已被其他请求更新，请重新打开后再确认')
        result = s.execute(update(TripRow).where(TripRow.id == trip.id, TripRow.revision == row.revision)
                           .values(revision=trip.revision, snapshot=raw))
        if result.rowcount != 1:
            latest = s.get(TripRow, trip.id, populate_existing=True)
            if (latest and latest.owner == user.id and latest.revision == trip.revision
                    and snapshot(Trip.model_validate_json(latest.snapshot)) == data):
                return trip.revision, False
            raise HTTPException(409, '旅行已被其他请求更新')
    s.add(VersionRow(id=str(uuid.uuid4()), trip_id=trip.id, revision=trip.revision, snapshot=raw, created=time.time()))
    if commit:s.commit()
    else:s.flush()
    return trip.revision, True


def locked_violations(before, after, *, shift_fixed_day=None):
    problems = []
    requirements = before.requirements
    anchors = {requirements.originPlaceId, requirements.destinationPlaceId} if requirements else set()
    for index, day in enumerate(before.days):
        for a in day.activities:
            if a.lock == 'none':
                continue
            match = next(((d, x) for d in after.days for x in d.activities if x.id == a.id), None)
            b = match[1] if match else None
            if not b or b.placeId != a.placeId or b.lock != a.lock:
                problems.append((a.id, '锁定或必须保留的地点不能移除或替换'))
            elif a.lock == 'fixed':
                time_changed = b.fixedStart != a.fixedStart
                can_shift = (
                    time_changed and index == shift_fixed_day and a.placeId not in anchors
                    and POIS[a.placeId]['kind'] != '返程'
                    and b.fixedStart >= a.fixedStart + (after.days[index].start - day.start)
                )
                if match[0].date != day.date or b.duration != a.duration or (time_changed and not can_shift):
                    problems.append((a.id, '固定安排的日期、时间与停留时长不能修改'))
    return problems


def route_body(trip, day):
    return RouteBody(mode=trip.mode, placeIds=[a.placeId for a in day.activities], **(trip.mobility.model_dump() if trip.mobility else {}))


def remember_place(place):
    known = POIS.get(place['id'])
    if known and (known.get('provider') != 'amap' or 'restrictedAccess' in known):
        return known
    with SessionLocal() as session:
        row = session.get(PlaceRecord, place['id'])
        if row:
            place = json.loads(row.data)
            if place.get('provider') == 'amap':
                place['restrictedAccess'] = restricted_parking(place)
                row.data = json.dumps(place, ensure_ascii=False)
                session.commit()
        else:
            session.add(PlaceRecord(id=place['id'], data=json.dumps(place, ensure_ascii=False)))
            session.commit()
    POIS[place['id']] = place
    return place

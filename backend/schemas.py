"""Request models shared by the API, planner and tests."""
from __future__ import annotations
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.planning import Requirements
from backend.places import POIS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Credentials(StrictModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^[\w\-]+$')
    password: str = Field(min_length=8, max_length=128)


class Activity(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    placeId: str
    duration: int = Field(ge=15, le=360)
    lock: Literal['none', 'keep', 'fixed'] = 'none'
    fixedStart: int | None = Field(default=None, ge=0, le=1439)

    @model_validator(mode='after')
    def known(self):
        if self.placeId not in POIS:
            raise ValueError('未知地点 ID')
        if self.lock == 'fixed' and self.fixedStart is None:
            raise ValueError('固定安排必须指定时间')
        return self


class Day(StrictModel):
    date: date
    start: int = Field(ge=0, le=1439)
    end: int = Field(ge=1, le=1440)
    activities: list[Activity] = Field(max_length=12)

    @model_validator(mode='after')
    def time_range(self):
        if self.end <= self.start:
            raise ValueError('结束时间必须晚于开始时间')
        return self


class Mobility(StrictModel):
    parkingByPlace: dict[str, str] = Field(default_factory=dict, max_length=36)
    parkingMinutes: int = Field(default=5, ge=0, le=30)

    @model_validator(mode='after')
    def valid(self):
        if any(id not in POIS or park not in POIS or POIS[park].get('kind') != '停车场' for id, park in self.parkingByPlace.items()):
            raise ValueError('停车点需要先搜索并确认')
        return self


class Trip(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=80)
    city: Literal['杭州'] = '杭州'
    people: int = Field(ge=1, le=10)
    mode: Literal['walk', 'drive'] = 'walk'
    days: list[Day] = Field(min_length=1, max_length=3)
    revision: int = Field(ge=1)
    validationStatus: Literal['draft', 'verified'] = 'draft'
    requirements: Requirements | None = None
    mobility: Mobility | None = None
    placeSnapshots: list[dict] = Field(default_factory=list, max_length=80)

    @model_validator(mode='after')
    def unique(self):
        ids = [a.id for d in self.days for a in d.activities]
        if len(ids) != len(set(ids)):
            raise ValueError('活动 ID 必须唯一')
        dates = [d.date for d in self.days]
        if len(set(dates)) != len(dates):
            raise ValueError('旅行日期不可重复')
        if self.requirements and (self.requirements.city != self.city or self.requirements.people != self.people or
                                   self.requirements.mode != self.mode or self.requirements.days != len(self.days)):
            raise ValueError('需求卡片与行程不一致')
        place_ids = [a.placeId for d in self.days for a in d.activities]
        place_ids += list(self.mobility.parkingByPlace.values()) if self.mobility else []
        self.placeSnapshots = [POIS[id] for id in dict.fromkeys(place_ids) if id.startswith(('osm:', 'amap:'))]
        return self


class TripBody(StrictModel):
    trip: Trip


class ConfirmBody(StrictModel):
    trip: Trip
    baseRevision: int = Field(ge=0)
    # Optional link to the planning run that produced this draft. The confirm
    # handler uses it to persist an authoritative `confirmed` event; existing
    # clients that omit it keep exactly the same behaviour.
    runId: str | None = Field(default=None, min_length=8, max_length=80,
                              pattern=r'^[A-Za-z0-9-]+$')


class SuggestionBody(StrictModel):
    trip: Trip
    day: int = Field(ge=0, le=2)
    prompt: str = Field(min_length=1, max_length=1000)


class ModelResult(StrictModel):
    summary: str = Field(max_length=300)
    changes: list[str] = Field(max_length=12)
    trip: Trip


class DayEdit(StrictModel):
    summary: str = Field(max_length=300)
    changes: list[str] = Field(max_length=12)
    start: int = Field(ge=0, le=1439)
    activities: list[Activity] = Field(max_length=12)


class CoordinatesBody(StrictModel):
    placeIds: list[str] = Field(min_length=1, max_length=12)


class RequestBody(StrictModel):
    text: str = Field(min_length=2, max_length=1000)


class ConversationStart(StrictModel):
    text: str = Field(min_length=2, max_length=1000)
    title: str = Field(default='我的杭州旅行', min_length=1, max_length=80)


class ConversationReply(StrictModel):
    version: int = Field(ge=1)
    requestId: str = Field(min_length=8, max_length=80, pattern=r'^[A-Za-z0-9-]+$')
    text: str = Field(default='', max_length=1000)
    choices: dict[str, str] = Field(default_factory=dict, max_length=6)
    confirm: bool = False
    retry: bool = False

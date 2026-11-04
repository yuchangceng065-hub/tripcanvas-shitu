"""Bounded OSRM adapter. Run one process/replica for the public FOSSGIS service.

One request contains all stops for a day; responses are cached for 24 hours.
No arbitrary upstream URL or user-supplied coordinates are accepted by the API.
"""
from __future__ import annotations
import asyncio
import math
import os
import time
from collections import OrderedDict
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class RouteBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['walk', 'drive']
    placeIds: list[str] = Field(min_length=2, max_length=12)
    parkingByPlace: dict[str,str] = Field(default_factory=dict,max_length=36)
    parkingMinutes: int = Field(default=5,ge=0,le=30)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid routing measurement')
    return value


def coordinate(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Invalid coordinate')
    lng, lat = value
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in value):
        raise ValueError('Invalid coordinate')
    if abs(lng) > 180 or abs(lat) > 90:
        raise ValueError('Coordinate out of range')
    return value


def normalize(data, body: RouteBody):
    if data.get('code') in ('NoRoute', 'NoSegment'):
        raise HTTPException(422, '这些地点之间暂无可用道路路线，请调整地点或交通方式')
    if data.get('code') != 'Ok':
        raise ValueError('Provider did not return a route')
    route = data['routes'][0]
    if len(route['legs']) != len(body.placeIds) - 1 or len(data['waypoints']) != len(body.placeIds):
        raise ValueError('Incomplete route')
    geometry = route['geometry']
    if geometry['type'] != 'LineString' or not 2 <= len(geometry['coordinates']) <= 100000:
        raise ValueError('Invalid geometry')
    coordinates = [coordinate(p) for p in geometry['coordinates']]
    return {
        'mode': body.mode, 'placeIds': body.placeIds, 'source': 'osrm',
        'legs': [{'distance': number(leg['distance']) / 1000,
                  'minutes': math.ceil(number(leg['duration']) / 60)} for leg in route['legs']],
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'waypoints': [{'location': coordinate(w['location']), 'distance': number(w['distance'])} for w in data['waypoints']],
        'fetchedAt': int(time.time() * 1000), 'cached': False,
    }


class Router:
    def __init__(self, client, pois, interval=1.1):
        self.client, self.pois, self.interval = client, pois, interval
        self.lock = asyncio.Lock()
        self.cache = OrderedDict()
        self.next_request = 0.0
        self.queued = 0

    async def route(self, body: RouteBody):
        if any(self.pois.get(id,{}).get('crs')=='GCJ02' for id in body.placeIds):raise HTTPException(422,'高德地点需要启用高德服务后算路，未使用其他坐标系替代')
        if any(id not in self.pois for id in body.placeIds):
            raise HTTPException(422, '包含未知地点')
        coordinates = ';'.join(f"{self.pois[id]['lng']},{self.pois[id]['lat']}" for id in body.placeIds)
        key = (body.mode, coordinates)
        if self.queued >= 8:
            raise HTTPException(503, '路线服务繁忙，请稍后重试')
        self.queued += 1
        try:
            async with self.lock:
                cached = self.cache.get(key)
                if cached and time.time() - cached['fetchedAt'] / 1000 < 86400:
                    self.cache.move_to_end(key)
                    return {**cached, 'placeIds': body.placeIds, 'cached': True}
                await asyncio.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + self.interval
                profile = 'foot' if body.mode == 'walk' else 'car'
                base = os.getenv(f'OSRM_{profile.upper()}_URL', f'https://routing.openstreetmap.de/routed-{profile}').rstrip('/')
                try:
                    response = await self.client.get(f'{base}/route/v1/driving/{coordinates}', params={
                        'overview': 'full', 'geometries': 'geojson', 'steps': 'false',
                        'alternatives': 'false', 'generate_hints': 'false',
                        'radiuses': ';'.join(['300' if profile == 'foot' else '1000'] * len(body.placeIds)),
                    })
                    if response.status_code == 429:
                        self.next_request = time.monotonic() + 60
                        raise HTTPException(503, '道路路线服务繁忙，请一分钟后重试')
                    data = response.json()
                    if data.get('code') in ('NoRoute', 'NoSegment'):
                        raise HTTPException(422, '这些地点之间暂无可用道路路线，请调整地点或交通方式')
                    response.raise_for_status()
                    result = normalize(data, body)
                except httpx.HTTPError:
                    raise HTTPException(503, '道路路线暂时无法加载，请稍后重试')
                except (ValueError, KeyError, IndexError, TypeError):
                    raise HTTPException(502, '道路服务返回了不完整的路线，请稍后重试')
                self.cache[key] = result
                self.cache.move_to_end(key)
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
                return result
        finally:
            self.queued -= 1

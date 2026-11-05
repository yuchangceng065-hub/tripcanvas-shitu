"""Scoped MCP tools for a single planning activation."""
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import time

import httpx
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from backend.amap import enabled as amap_enabled
from backend.store import remember_place

PlaceIds = Annotated[list[str], Field(min_length=1, max_length=8)]
Days = Annotated[list[PlaceIds], Field(min_length=1, max_length=3)]
Plans = Annotated[list[Days], Field(min_length=2, max_length=3)]


def build_tool_server(services, toolbox=None):
    server = FastMCP('TripCanvas planning tools', json_response=True,
                     stateless_http=True, log_level='WARNING')

    async def run(action, args):
        if toolbox is None:
            return {'error': '请先确认地点和需求', 'statusCode': 422}
        try:
            outcome = await toolbox.run(action, args)
        except HTTPException as error:
            return {'error': str(error.detail)[:300], 'statusCode': error.status_code}
        except (ValueError, TypeError, KeyError) as error:
            return {'error': str(error)[:300], 'statusCode': 422}
        if 'trip' in outcome:
            outcome['trip'] = outcome['trip'].model_dump(mode='json')
        return outcome

    @server.tool(structured_output=True)
    async def search_places(query: Annotated[str, Field(min_length=1, max_length=100)]) -> dict[str, Any]:
        """Find up to three real places in Hangzhou for the user to confirm."""
        searcher = services.amap if amap_enabled() else services.search
        try:
            return {'places': [remember_place(place) for place in (await searcher.search(query))[:3]]}
        except HTTPException as error:
            return {'error': str(error.detail)[:300], 'statusCode': error.status_code}

    @server.tool(structured_output=True)
    async def route_day(placeIds: PlaceIds, mode: Literal['walk', 'drive']) -> dict[str, Any]:
        """Query road estimates using confirmed stops and the locked transport mode."""
        if toolbox is not None and mode != toolbox.mode:
            return {'error': '交通方式须与已确认需求一致', 'statusCode': 422}
        return await run('route_day', {'placeIds': placeIds})

    @server.tool(structured_output=True)
    async def validate_plan(days: Days) -> dict[str, Any]:
        """Check candidate identity, required places, road estimates and time constraints."""
        return await run('validate_plan', {'days': days})

    @server.tool(structured_output=True)
    async def compare_plans(plans: Plans) -> dict[str, Any]:
        """Compare two or three candidate itineraries under the same constraints."""
        return await run('compare_plans', {'plans': plans})

    @server.tool(structured_output=True)
    async def repair_plan(seedDays: Days) -> dict[str, Any]:
        """Search for a feasible itinerary within confirmed constraints and a fixed budget."""
        return await run('repair_plan', {'seedDays': seedDays})

    return server


class PlanningToolClient:
    def __init__(self, session, available, observer=None):
        self.session = session
        self.available = available
        self.observer = observer

    async def call(self, name, arguments):
        if name not in self.available:
            raise ValueError('该规划工具不可用')
        started = time.monotonic()
        success = False
        try:
            response = await self.session.call_tool(name, arguments)
            result = response.structuredContent
            if response.isError or not isinstance(result, dict):
                raise ValueError('工具参数或返回结构无法通过核验')
            if 'statusCode' in result:
                raise HTTPException(result['statusCode'], result['error'])
            success = result.get('ok', True)
            return result
        finally:
            if self.observer is not None:
                self.observer(name, success, round((time.monotonic() - started) * 1000))

    async def search(self, query):
        return (await self.call('search_places', {'query': query}))['places']

    async def plan(self, action, args):
        from backend.schemas import Trip

        name = 'validate_plan' if action == 'propose_plan' else action
        arguments = {'days': args.get('days')} if name == 'validate_plan' else args
        result = await self.call(name, arguments)
        if 'trip' in result:
            result['trip'] = Trip.model_validate(result['trip'])
            result['routes'] = {int(index): route for index, route in result['routes'].items()}
        return result


@asynccontextmanager
async def connect_tools(server, observer=None):
    # Use the actual MCP protocol over an in-process HTTP transport. The scoped
    # server only exists for this activation and cannot be reached externally.
    app = server.streamable_http_app()
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8000') as http:
            async with streamable_http_client('http://127.0.0.1:8000/mcp', http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    yield PlanningToolClient(session, {tool.name for tool in tools.tools}, observer)

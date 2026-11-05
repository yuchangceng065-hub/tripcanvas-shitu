#!/usr/bin/env python3
"""Inspect the planning tools with a separate MCP client and a synthetic server."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def serve():
    os.environ.update(DATABASE_URL='sqlite:///:memory:', AMAP_WEB_KEY='', PLANNER_MODE='rules')
    from backend.agent import Toolbox
    from backend.agent_mcp import build_tool_server
    from backend.places import POIS
    from backend.planning import AlternativesBody

    async def search(query):
        return [place for place in POIS.values() if query in place['name']][:3]

    async def route(body):
        return {'source': 'osrm', 'fixture': True, 'legs': [
            {'minutes': 10, 'distance': .6} for _ in body.placeIds[1:]
        ]}

    scope = AlternativesBody.model_validate({
        'title': 'MCP 协议验收', 'candidateIds': ['quyuan', 'museum'],
        'requirements': {'city': '杭州', 'startDate': '2026-11-14', 'days': 1,
                         'people': 2, 'mode': 'walk', 'mustVisitIds': ['quyuan', 'museum']},
    })
    server = build_tool_server(SimpleNamespace(search=SimpleNamespace(search=search)),
                               Toolbox(scope, SimpleNamespace(route=route)))
    await server.run_stdio_async()


async def inspect():
    process = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), '--server'])
    async with stdio_client(process) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            available = await session.list_tools()
            valid = await session.call_tool('validate_plan', {'days': [['quyuan', 'museum']]})
            invalid = await session.call_tool('validate_plan', {'days': [['quyuan', 'station']]})
            assert valid.structuredContent['ok'] and invalid.structuredContent['statusCode'] == 422
            print(json.dumps({
                'protocol': info.protocolVersion, 'transport': 'stdio', 'roads': 'synthetic',
                'tools': sorted(tool.name for tool in available.tools),
                'validated': valid.structuredContent['ok'], 'unknownCandidateRejected': True,
            }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(serve() if sys.argv[1:] == ['--server'] else inspect())

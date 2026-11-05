"""LangGraph orchestration for the site's existing conversation API."""
from contextlib import asynccontextmanager
from typing import TypedDict

from fastapi import HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from backend.agent import AgentSteps
from backend.agent_mcp import build_tool_server, connect_tools
from backend.conversation import ground_places, review_intake, understand
from backend.db import engine
from backend.planning import AlternativesBody


@asynccontextmanager
async def graph_checkpointer():
    if engine.dialect.name == 'postgresql':
        url = engine.url.set(drivername='postgresql').render_as_string(hide_password=False)
        async with AsyncConnectionPool(
            url, min_size=1, max_size=2,
            kwargs={'autocommit': True, 'row_factory': dict_row, 'prepare_threshold': None,
                    'connect_timeout': 10},
        ) as pool:
            await pool.wait(timeout=15)
            saver = AsyncPostgresSaver(pool)
            await saver.setup()
            yield saver
    else:
        database = engine.url.database
        path = f'{database}.agent.db' if database and database != ':memory:' else ':memory:'
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            await saver.setup()
            yield saver


class GraphState(TypedDict, total=False):
    version: int
    status: str
    next_node: str
    decision: dict | None


async def run_conversation_graph(services, recorder, version, session, persist):
    def enter(node):
        recorder.event('graph_node', detail={'node': node, 'engine': 'langgraph'})

    def save_step(checkpoint):
        session['checkpoint'] = checkpoint
        persist()

    def reserve_repair():
        if session.get('repairUsed'):
            raise ValueError('本任务的约束求解次数已用完')
        session['repairUsed'] = True
        persist()

    steps = None
    if session['phase'] == 'agent':
        steps = AgentSteps(
            AlternativesBody.model_validate(session['data']), services.router, recorder,
            checkpoint=session.get('checkpoint'), persist=save_step, reserve_repair=reserve_repair,
            preferences='\n'.join(turn['text'] for turn in session['turns'] if turn['role'] == 'user'),
        )

    async def dispatch(_state):
        return {'next_node': 'understand' if session['phase'] == 'intake' else 'choose_action'}

    async def extract(_state):
        enter('understand')
        await understand(session, recorder, persist)
        return {}

    async def search(_state):
        enter('search_places')
        await ground_places(session, services, recorder, persist, tools.search)
        return {}

    async def review(_state):
        enter('review_requirements')
        status = review_intake(session, recorder)
        persist()
        return {'status': status}

    async def choose(_state):
        enter('choose_action')
        if steps.step >= steps.limit:
            result = steps.incomplete()
            return {'status': result['status'], 'next_node': END}
        decision = await steps.decide()
        return {'decision': decision, 'next_node': 'execute_tool' if decision else 'choose_action'}

    async def execute(graph_state):
        enter('execute_tool')
        result = await steps.act(graph_state['decision'])
        if result is None:
            return {'next_node': 'choose_action'}
        status = result['status']
        if status == 'needs_input':
            session['question'] = result['question']
            session['turns'].append({'role': 'assistant', 'text': result['question']})
            persist()
            return {'status': status, 'next_node': 'wait_for_input'}
        return {'status': status, 'next_node': END}

    def wait_for_input(graph_state):
        # LangGraph re-enters this node from the start on resume.
        answer = interrupt({'status': graph_state['status'], 'version': graph_state['version']})
        if answer['version'] != version or version <= graph_state['version']:
            raise HTTPException(409, '对话回复版本已变化，请刷新')
        return {'version': version, 'status': 'running', 'decision': None}

    builder = StateGraph(GraphState)
    for name, node in (
        ('dispatch', dispatch), ('understand', extract), ('search_places', search),
        ('review_requirements', review), ('choose_action', choose), ('execute_tool', execute),
        ('wait_for_input', wait_for_input),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, 'dispatch')
    builder.add_conditional_edges('dispatch', lambda state: state['next_node'],
                                  ['understand', 'choose_action'])
    builder.add_edge('understand', 'search_places')
    builder.add_edge('search_places', 'review_requirements')
    builder.add_edge('review_requirements', 'wait_for_input')
    builder.add_conditional_edges('choose_action', lambda state: state['next_node'],
                                  ['execute_tool', 'choose_action', END])
    builder.add_conditional_edges('execute_tool', lambda state: state['next_node'],
                                  ['choose_action', 'wait_for_input', END])
    builder.add_edge('wait_for_input', 'dispatch')
    graph = builder.compile(checkpointer=services.graph_checkpointer)
    config = {'configurable': {'thread_id': recorder.id}, 'recursion_limit': 32}
    server = build_tool_server(services, steps.toolbox if steps else None)
    # Production graph checkpoints stay in the app's database. Explicitly
    # disable SDK tracing here, including when a developer has tracing enabled.
    def record_tool(name, success, duration_ms):
        recorder.event('mcp_call', status='ok' if success else 'error',
                       detail={'tool': name, 'ok': success, 'durationMs': duration_ms,
                               'transport': 'streamable-http'})

    error = None
    with tracing_context(enabled=False, parent=False):
        async with connect_tools(server, record_tool) as tools:
            if steps:
                steps.call_tool = tools.plan
            snapshot = await graph.aget_state(config)
            if snapshot.interrupts:
                recorder.event('graph_resume', detail={'engine': 'langgraph', 'version': version})
                graph_input = Command(resume={'version': version})
            else:
                graph_input = {'version': version, 'status': 'running'}
            try:
                result = await graph.ainvoke(graph_input, config)
            except HTTPException as failure:
                error = failure
    if error is not None:
        raise error
    if result.get('__interrupt__'):
        recorder.event('graph_pause', detail={'status': result['status'], 'engine': 'langgraph'})
    return result['status']

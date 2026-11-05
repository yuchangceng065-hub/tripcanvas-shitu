"""Bounded tool-calling planning agent.

The model only chooses actions; every tool runs server-side and the final plan
must pass the same deterministic validation used by /planner/confirm.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

from fastapi import HTTPException

from backend.alternatives import PlanSearch, metrics
from backend.model_gateway import model_json_messages
from backend.planning import evaluate, make_trip, walking_km
from backend.places import POIS
from backend.routing import RouteBody
from backend.schemas import Trip
from backend.store import snapshot
from backend.runlog import encode_detail
from backend.agent_schema import ACTIONS,decision_format

MAX_STEPS = int(__import__('os').getenv('PLAN_AGENT_STEPS', '6'))
MAX_TOTAL_STEPS = 12

SYSTEM = '''你是杭州行程规划 Agent。每一步只输出一个 JSON 对象，不要 Markdown，不要解释。
这是应用层 JSON 动作调度，不使用供应商原生 tool_calls；不要输出工具调用标记或分析文本。
可用动作：
{"action":"route_day","args":{"placeIds":["id","id"],"mode":"walk|drive"}} 查询一天的真实道路
{"action":"validate_plan","args":{"days":[["id","id"]],"summary":"待核验安排"}} 校验一套分日安排
{"action":"compare_plans","args":{"plans":[[["id"],["id"]]]}} 比较两套安排
{"action":"propose_plan","args":{"days":[["id","id"]],"summary":"一句话说明"}} 提交最终方案
{"action":"need_input","args":{"question":"需要用户确认的问题"}} 无法继续时反问
{"action":"give_up","args":{"reason":"原因"}} 放弃并说明
规则：只能使用候选 id；必去地点每天合计恰好一次；天数必须等于需求天数；每天 1—8 站；提出方案前先 route_day 或 validate_plan；观察结果 ok=false 时调整；最多 6 步；不编造地点、坐标或时间。
重要：days只能包含candidates列表中的游览地点ID。requirements里的originPlaceId/destinationPlaceId是程序自动注入的起终点，不能出现在days里。route_day可使用这两个锚点；validate_plan/compare_plans/propose_plan不要包含锚点。'''


class Toolbox:
    def __init__(self, data, router):
        self.data = data
        self.router = router
        self.mode = data.requirements.mode
        self.routes = {}
        self.candidates = set(data.candidateIds)
        self.anchors = {id for id in (data.requirements.originPlaceId, data.requirements.destinationPlaceId) if id}

    def _ids(self, ids, *, allow_anchors=False):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8:
            raise ValueError('placeIds 需要 1—8 个候选 id')
        allowed = self.candidates | (self.anchors if allow_anchors else set())
        if any(not isinstance(id, str) or id not in allowed or id not in POIS for id in ids):
            raise ValueError('包含未知地点或未确认地点，只能使用本次候选和明确的起终点')
        visits = [id for id in ids if id not in self.anchors]
        if len(visits) != len(set(visits)):
            raise ValueError('游览地点不可重复安排')
        return ids

    async def _route(self, ids):
        key = tuple(ids)
        if key not in self.routes:
            self.routes[key] = await self.router.route(RouteBody(mode=self.mode, placeIds=list(ids)))
        return self.routes[key]

    def _trip(self, days):
        if not isinstance(days, list) or len(days) != self.data.requirements.days:
            raise ValueError(f'需要 {self.data.requirements.days} 天的分日安排')
        for day in days:
            self._ids(day)
        flat = [id for day in days for id in day]
        if len(flat) != len(set(flat)):
            raise ValueError('游览地点不可跨日重复安排')
        required = self.candidates - set(self.data.optionalIds)
        if not required <= set(flat):
            raise ValueError('必去地点必须全部保留且恰好安排一次')
        body = SimpleNamespace(title=self.data.title, requirements=self.data.requirements)
        return Trip.model_validate(make_trip(body, [list(day) for day in days], POIS))

    async def _check(self, days):
        trip = self._trip(days)
        routes = {}
        for index, day in enumerate(trip.days):
            if len(day.activities) > 1:
                routes[index] = await self._route([activity.placeId for activity in day.activities])
        issues = evaluate(trip, routes, POIS)
        summary = metrics(trip, routes)
        return trip, routes, issues, summary

    async def run(self, action, args):
        if action == 'route_day':
            ids = self._ids(args.get('placeIds'), allow_anchors=True)
            route = await self._route(ids)
            return {'ok': True, 'summary': {'minutes': sum(leg['minutes'] for leg in route['legs']),
                                            'walkingKm': walking_km(route, self.mode),
                                            'legs': [{'minutes': leg['minutes'], 'distance': round(leg['distance'], 3)} for leg in route['legs']],
                                            'source': route.get('source')}}
        if action in ('validate_plan', 'propose_plan'):
            trip, routes, issues, summary = await self._check(args.get('days'))
            errors = [issue for issue in issues if issue['severity'] == 'error']
            return {'ok': not errors, 'summary': {'feasible': not errors, 'issues': issues[:6], 'metrics': summary},
                    'trip': trip, 'routes': routes, 'issues': issues}
        if action == 'compare_plans':
            plans = args.get('plans')
            if not isinstance(plans, list) or not 2 <= len(plans) <= 3:
                raise ValueError('需要 2—3 套分日安排')
            compared = []
            for days in plans:
                _, _, issues, summary = await self._check(days)
                compared.append({'feasible': not [issue for issue in issues if issue['severity'] == 'error'],
                                 'issues': [issue for issue in issues if issue['severity'] == 'error'][:4], 'metrics': summary})
            return {'ok': True, 'summary': compared}
        if action == 'repair_plan':
            seed = args.get('seedDays')
            self._trip(seed)
            search = PlanSearch(self.data, POIS, POIS, self.router.route, Trip.model_validate,
                                max_routes=5, max_states=10, seconds=25)
            found = await search.search(seed)
            if not found['options']:
                return {'ok': False, 'summary': {'feasible': False, 'search': found['search'],
                        'issues': (found['diagnostic'] or {}).get('issues', [])[:6]}}
            option = found['options'][0]
            return {'ok': True, 'trip': Trip.model_validate(option['trip']),
                    'routes': option['routes'], 'issues': option['issues'],
                    'summary': {'feasible': True, 'metrics': option['metrics'], 'search': found['search']}}
        raise ValueError('未知动作：' + str(action))


def _observation(value):
    return encode_detail(value, limit=3500)


def validate_agent_request(data):
    ids = data.candidateIds
    anchors = [id for id in (data.requirements.originPlaceId, data.requirements.destinationPlaceId) if id]
    if len(ids) != len(set(ids)) or any(id not in POIS for id in ids + anchors):
        raise HTTPException(422, '请先确认所有地点，候选不可重复或包含未知地点')
    if set(ids) & set(anchors):
        raise HTTPException(422, '起终点请从游览候选中移除，避免重复安排')
    if len(ids) < data.requirements.days:
        raise HTTPException(422, '候选地点数量不能少于旅行天数')


def timeline(trip, routes):
    """Verified road arithmetic for the read-only confirmation preview."""
    days = []
    for index, day in enumerate(trip.days):
        clock = day.start; rows = []
        for position, activity in enumerate(day.activities):
            travel = routes[index]['legs'][position - 1]['minutes'] if position else 0
            earliest = clock + travel
            start = max(earliest, activity.fixedStart) if activity.lock == 'fixed' else earliest
            clock = start + activity.duration
            rows.append({'activityId': activity.id, 'placeId': activity.placeId, 'start': start, 'end': clock,
                         'travelMinutes': travel, 'waitingMinutes': start - earliest, 'duration': activity.duration,
                         'lock': activity.lock, 'fixedStart': activity.fixedStart})
        days.append({'date': str(day.date), 'start': day.start, 'end': day.end, 'activities': rows})
    return days


class AgentSteps:
    """Shared model and tool steps for legacy runs and the LangGraph workflow."""

    def __init__(self, data, router, recorder, *, checkpoint=None, persist=None,
                 preferences='', tool_runner=None, reserve_repair=None):
        validate_agent_request(data)
        data.requirements.mustVisitIds = [id for id in data.candidateIds if id not in data.optionalIds]
        self.data = data
        self.recorder = recorder
        self.persist = persist
        self.reserve_repair = reserve_repair
        self.toolbox = Toolbox(data, router)
        self.call_tool = tool_runner or self.toolbox.run
        self.actions = (*ACTIONS, 'repair_plan') if reserve_repair else ACTIONS
        candidates = [{'id': id, 'name': POIS[id]['name'], 'kind': POIS[id].get('kind', '地点'),
                       'required': id in data.requirements.mustVisitIds} for id in data.candidateIds]
        self.messages = list(checkpoint['messages']) if checkpoint else [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps({
                'requirements': data.requirements.model_dump(mode='json'),
                'candidates': candidates,
                'optionalIds': data.optionalIds,
                'userPreferences': preferences[:6000],
            }, ensure_ascii=False)},
        ]
        self.messages[0] = {'role': 'system', 'content': SYSTEM + (
            '\n若验证显示冲突，可调用一次 {"action":"repair_plan","args":{"seedDays":[["id"]]}}。'
            'seedDays 必须是已核对的游览候选顺序，程序将尝试换序、分日和可选地点取舍。'
            if reserve_repair else ''
        )}
        self.step = checkpoint.get('step', 0) if checkpoint else 0
        self.limit = min(self.step + MAX_STEPS, MAX_TOTAL_STEPS)
        self.output_format = decision_format(data.candidateIds, sorted(self.toolbox.anchors),
                                             repair=bool(reserve_repair))

    def save(self):
        if self.persist:
            self.persist({'messages': self.messages, 'step': self.step})

    def observe(self, decision, result):
        self.messages.extend([
            {'role': 'assistant', 'content': _observation(decision)},
            {'role': 'user', 'content': 'observation: ' + _observation(result)},
        ])
        self.save()

    async def decide(self):
        self.step += 1
        self.save()
        try:
            return await model_json_messages(self.messages, response_format=self.output_format)
        except (ValueError, TypeError) as error:
            self.recorder.event('model_error', status='error',
                                detail={'step': self.step, 'error': str(error)[:200]})
            self.messages.append({'role': 'user', 'content': 'observation: 输出不是有效的动作 JSON，请只输出一个 JSON 对象。'})
            self.save()
            return None

    async def act(self, decision):
        action = decision.get('action')
        args = decision.get('args') if isinstance(decision.get('args'), dict) else {}
        step = self.step
        recorder = self.recorder
        recorder.event('model', detail={'step': step, 'action': action})
        if action not in self.actions:
            recorder.event('tool_result', status='error',
                           detail={'step': step, 'ok': False, 'error': '未知动作'})
            self.observe(decision, {'ok': False, 'error': '未知动作，可用动作：' + ', '.join(self.actions)})
            return None
        if action == 'need_input':
            if step >= MAX_TOTAL_STEPS:
                return self.incomplete()
            question = str(args.get('question', ''))[:300]
            self.messages.append({'role': 'assistant', 'content': _observation(decision)})
            self.save()
            recorder.finish('needs_input', {'question': question, 'modelSteps': step})
            return {'runId': recorder.id, 'status': 'needs_input', 'question': question}
        if action == 'give_up':
            reason = str(args.get('reason', '未说明'))[:300]
            recorder.finish('gave_up', {'message': reason})
            return {'runId': recorder.id, 'status': 'gave_up', 'message': reason}
        recorder.event('tool_call', detail={'step': step, 'tool': action, 'args': args})
        try:
            if action == 'repair_plan':
                self.reserve_repair()
            outcome = await self.call_tool(action, args)
        except (ValueError, KeyError, TypeError, HTTPException) as error:
            detail = str(getattr(error, 'detail', None) or error)[:300]
            recorder.event('tool_result', status='error',
                           detail={'step': step, 'ok': False, 'error': detail})
            self.observe(decision, {'ok': False, 'error': detail})
            return None
        if action in ('propose_plan', 'repair_plan') and outcome['ok']:
            return self.complete(action, args, outcome)
        recorder.event('tool_result', detail={'step': step, 'ok': outcome['ok'], 'summary': outcome['summary']})
        self.observe(decision, {'ok': outcome['ok'], 'summary': outcome['summary']})
        return None

    def complete(self, action, args, outcome):
        trip = outcome['trip']
        routes = outcome['routes']
        visits = [activity.placeId for day in trip.days for activity in day.activities]
        validation = {
            'trip': snapshot(trip), 'issues': outcome['issues'], 'metrics': outcome['summary']['metrics'],
            'summary': '约束求解器已找到并核验修复方案' if action == 'repair_plan' else str(args.get('summary', ''))[:300],
            'timeline': timeline(trip, routes),
            'includedIds': [id for id in visits if id in self.toolbox.candidates],
            'omittedIds': [id for id in self.data.candidateIds if id not in visits],
            'routeSources': sorted({route.get('source', 'unknown') for route in routes.values()}),
        }
        summary = {'feasible': True}
        if action == 'repair_plan':
            summary['search'] = outcome['summary']['search']
        self.recorder.event('tool_result', detail={'step': self.step, 'ok': True, 'summary': summary})
        self.recorder.complete({'runId': self.recorder.id, 'status': 'completed',
                                'validation': validation, 'modelSteps': self.step})
        return {'runId': self.recorder.id, 'status': 'completed', 'plan': validation, 'steps': self.step}

    def incomplete(self):
        message = f'本轮 {MAX_STEPS} 步或累计 {MAX_TOTAL_STEPS} 步预算已用完，仍未给出可行方案'
        self.recorder.finish('incomplete', {'message': message})
        return {'runId': self.recorder.id, 'status': 'incomplete', 'message': message}


async def run_agent(data, router, recorder, *, checkpoint=None, persist=None, preferences=''):
    """Keep the original entry point for already-created tasks."""
    steps = AgentSteps(data, router, recorder, checkpoint=checkpoint,
                       persist=persist, preferences=preferences)
    while steps.step < steps.limit:
        decision = await steps.decide()
        if decision is not None:
            result = await steps.act(decision)
            if result is not None:
                return result
    return steps.incomplete()

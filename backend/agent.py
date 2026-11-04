"""Bounded tool-calling planning agent.

The model only chooses actions; every tool runs server-side and the final plan
must pass the same deterministic validation used by /planner/confirm.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

from fastapi import HTTPException

from backend.alternatives import metrics
from backend.model_gateway import model_json_messages
from backend.planning import evaluate, make_trip, walking_km
from backend.places import POIS
from backend.routing import RouteBody
from backend.schemas import Trip
from backend.store import snapshot
from backend.runlog import encode_detail
from backend.agent_schema import ACTIONS,decision_format

MAX_STEPS = int(__import__('os').getenv('PLAN_AGENT_STEPS', '6'))

SYSTEM = '''你是杭州行程规划 Agent。每一步只输出一个 JSON 对象，不要 Markdown，不要解释。
这是应用层 JSON 动作调度，不使用供应商原生 tool_calls；不要输出工具调用标记或分析文本。
可用动作：
{"action":"route_day","args":{"placeIds":["id","id"],"mode":"walk|drive"}} 查询一天的真实道路
{"action":"validate_plan","args":{"days":[["id","id"]],"summary":"待核验安排"}} 校验一套分日安排
{"action":"compare_plans","args":{"plans":[[["id"],["id"]]]}} 比较两套安排
{"action":"propose_plan","args":{"days":[["id","id"]],"summary":"一句话说明"}} 提交最终方案
{"action":"need_input","args":{"question":"需要用户确认的问题"}} 无法继续时反问
{"action":"give_up","args":{"reason":"原因"}} 放弃并说明
规则：只能使用候选 id；必去地点每天合计恰好一次；天数必须等于需求天数；每天 1—8 站；提出方案前先 route_day 或 validate_plan；观察结果 ok=false 时调整；最多 6 步；不编造地点、坐标或时间。'''


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


async def run_agent(data, router, recorder):
    """Execute up to MAX_STEPS model decisions; returns the public result dict."""
    validate_agent_request(data)
    data.requirements.mustVisitIds = [id for id in data.candidateIds if id not in data.optionalIds]
    candidates = [{'id': id, 'name': POIS[id]['name'], 'kind': POIS[id].get('kind', '地点'),
                   'required': id in data.requirements.mustVisitIds} for id in data.candidateIds]
    messages = [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': json.dumps({'requirements': data.requirements.model_dump(mode='json'),
                                                'candidates': candidates, 'optionalIds': data.optionalIds}, ensure_ascii=False)},
    ]
    toolbox = Toolbox(data, router)
    output_format = decision_format(data.candidateIds, sorted(toolbox.anchors))
    for step in range(1, MAX_STEPS + 1):
        try:
            decision = await model_json_messages(messages, response_format=output_format)
        except HTTPException:
            raise
        except (ValueError, TypeError) as error:
            recorder.event('model_error', status='error', detail={'step': step, 'error': str(error)[:200]})
            messages.append({'role': 'user', 'content': 'observation: 输出不是有效的动作 JSON，请只输出一个 JSON 对象。'})
            continue
        action = decision.get('action')
        args = decision.get('args') if isinstance(decision.get('args'), dict) else {}
        recorder.event('model', detail={'step': step, 'action': action})
        if action not in ACTIONS:
            recorder.event('tool_result', status='error', detail={'step': step, 'ok': False, 'error': '未知动作'})
            messages += [{'role': 'assistant', 'content': _observation(decision)},
                         {'role': 'user', 'content': 'observation: ' + _observation({'ok': False, 'error': '未知动作，可用动作：' + ', '.join(ACTIONS)})}]
            continue
        if action == 'need_input':
            question = str(args.get('question', ''))[:300]
            recorder.finish('needs_input', {'question': question})
            return {'runId': recorder.id, 'status': 'needs_input', 'question': question}
        if action == 'give_up':
            reason = str(args.get('reason', '未说明'))[:300]
            recorder.finish('gave_up', {'message': reason})
            return {'runId': recorder.id, 'status': 'gave_up', 'message': reason}
        recorder.event('tool_call', detail={'step': step, 'tool': action, 'args': args})
        try:
            outcome = await toolbox.run(action, args)
        except (ValueError, KeyError, TypeError, HTTPException) as error:
            detail = getattr(error, 'detail', None) or str(error)
            recorder.event('tool_result', status='error', detail={'step': step, 'ok': False, 'error': str(detail)[:300]})
            messages += [{'role': 'assistant', 'content': _observation(decision)},
                         {'role': 'user', 'content': 'observation: ' + _observation({'ok': False, 'error': str(detail)[:300]})}]
            continue
        if action == 'propose_plan' and outcome['ok']:
            trip, issues = outcome['trip'], outcome['issues']
            validation = {'trip': snapshot(trip), 'issues': issues, 'metrics': outcome['summary']['metrics'],
                          'summary': str(args.get('summary', ''))[:300], 'timeline': timeline(trip, outcome['routes']),
                          'includedIds': [a.placeId for d in trip.days for a in d.activities if a.placeId in toolbox.candidates],
                          'omittedIds': [id for id in data.candidateIds if not any(a.placeId == id for d in trip.days for a in d.activities)],
                          'routeSources': sorted({r.get('source', 'unknown') for r in outcome['routes'].values()})}
            recorder.event('tool_result', detail={'step': step, 'ok': True, 'summary': {'feasible': True}})
            recorder.complete({'runId': recorder.id, 'status': 'completed', 'validation': validation, 'modelSteps': step})
            return {'runId': recorder.id, 'status': 'completed', 'plan': validation, 'steps': step}
        recorder.event('tool_result', detail={'step': step, 'ok': outcome['ok'], 'summary': outcome['summary']})
        messages += [{'role': 'assistant', 'content': _observation(decision)},
                     {'role': 'user', 'content': 'observation: ' + _observation({'ok': outcome['ok'], 'summary': outcome['summary']})}]
    message = f'超过 {MAX_STEPS} 步仍未给出可行方案'
    recorder.finish('incomplete', {'message': message})
    return {'runId': recorder.id, 'status': 'incomplete', 'message': message}

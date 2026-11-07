"""Trajectory-level evaluation for Agent runs.

Independent oracle: this module never imports the planner, the agent, the
conversation engine or their validators. It judges two separate things:

  * process  - was the *behaviour* correct, based on the persisted event trace?
  * outcome  - was the *produced plan* correct?

A case passes only when both are correct. A correct plan reached through an
unconfirmed place, an unknown tool, a duplicate call or a write after failure
is recorded as `result_only` and counted as a failure. This is the difference
between result evaluation and trajectory evaluation.

The suite also reports proposalRate/refusalRate so a system cannot score well
by refusing every task.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

SUITE_VERSION = 'trajectory-eval/v1'
FINAL_PHASES = {'completed', 'failed', 'interrupted', 'gave_up',
                'incomplete', 'cancelled'}
# A multi-turn run may pause and resume several times; only one FINAL event is allowed.
PAUSE_PHASES = {'needs_input', 'needs_confirmation'}
TERMINAL_PHASES = FINAL_PHASES | PAUSE_PHASES
# LangGraph records a checkpoint/pause event AFTER the terminal event; it is
# metadata, not a second terminal state.
TRAILING_META_PHASES = {'graph_pause'}
# `/planner/confirm` records write evidence AFTER the run reached its final
# state. It is an authoritative marker, not an Agent step: at most one, always
# the last event, and only ever on a completed run.
CONFIRMATION_PHASES = {'confirmed', 'confirm', 'apply_confirmed'}
DEFAULT_MCP_TOOLS = ['search_places', 'route_day', 'validate_plan',
                     'compare_plans', 'repair_plan']
DEFAULT_MAX_SEARCHES = 12
DEFAULT_MAX_REFINE_ROUNDS = 2


def suite_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_suite(suite: dict) -> list[dict]:
    cases = suite.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a non-empty list')
    if suite.get('suite') != SUITE_VERSION:
        raise ValueError('unknown trajectory suite version')
    seen = set()
    for case in cases:
        cid = case.get('id')
        if not isinstance(cid, str) or cid in seen:
            raise ValueError('missing or duplicate case id')
        seen.add(cid)
        if case.get('split') not in ('development', 'regression'):
            raise ValueError(f'{cid}: invalid split')
        if not isinstance(case.get('expectedPass'), bool):
            raise ValueError(f'{cid}: expectedPass must be a human-labelled boolean')
        if not case.get('title'):
            raise ValueError(f'{cid}: missing title')
        expect = case.get('expect')
        if not isinstance(expect, dict) or not expect.get('terminal'):
            raise ValueError(f'{cid}: missing expected terminal')
        if not isinstance(case.get('events'), list) or not case['events']:
            raise ValueError(f'{cid}: events must be a non-empty list')
        if not isinstance(case.get('result'), dict) or not case['result'].get('status'):
            raise ValueError(f'{cid}: missing run result')
        for event in case['events']:
            if not all(key in event for key in ('seq', 'phase', 'status')):
                raise ValueError(f'{cid}: malformed event')
    return cases


def _detail(event: dict) -> dict:
    value = event.get('detail')
    return value if isinstance(value, dict) else {}


def _places_in_args(args) -> list[str]:
    """Only read known place-bearing fields; never guess from free text."""
    if not isinstance(args, dict):
        return []
    places: list[str] = []
    for key in ('days', 'seedDays'):
        value = args.get(key)
        if isinstance(value, list):
            for day in value:
                if isinstance(day, list):
                    places.extend(str(item) for item in day)
                elif isinstance(day, str):
                    places.append(day)
    for key in ('placeId', 'placeIds', 'candidateIds', 'optionalIds', 'mustVisitIds'):
        value = args.get(key)
        if isinstance(value, str):
            places.append(value)
        elif isinstance(value, list):
            places.extend(str(item) for item in value)
    return [place for place in places if place]


def _places_in_plan(plan) -> list[str]:
    if not isinstance(plan, dict):
        return []
    places: list[str] = []
    for day in plan.get('days', []):
        if isinstance(day, list):
            places.extend(str(item) for item in day)
        elif isinstance(day, dict):
            for activity in day.get('activities', []):
                if isinstance(activity, dict) and activity.get('placeId'):
                    places.append(str(activity['placeId']))
    return places


def _process_checks(case: dict) -> list[dict]:
    events = case['events']
    result = case['result']
    expect = case['expect']
    checks: list[dict] = []

    def add(name, passed, reason=''):
        checks.append({'name': name, 'passed': bool(passed), 'reason': reason})

    seqs = [event.get('seq') for event in events]
    ordered = all(isinstance(value, int) for value in seqs) and seqs == sorted(set(seqs))
    sequence_ok = ordered and (not seqs or seqs[0] >= 1)
    add('event_sequence_monotonic', sequence_ok,
        '' if sequence_ok else f'seq={seqs}')

    terminal = result.get('status')
    add('terminal_matches_expectation', terminal == expect['terminal'],
        f'got={terminal} expected={expect["terminal"]}')

    confirmation_events = [event for event in events
                           if event['phase'] in CONFIRMATION_PHASES]
    trailing_confirmation = (bool(confirmation_events)
                             and events[-1]['phase'] in CONFIRMATION_PHASES)
    confirmation_valid = (len(confirmation_events) <= 1
                          and (not confirmation_events or trailing_confirmation)
                          and (not confirmation_events or terminal == 'completed'))
    add('confirmation_event_is_trailing', confirmation_valid,
        f'count={len(confirmation_events)} terminal={terminal}')
    # Terminal-order checks look at the Agent trace only; the confirmation
    # marker is deliberately outside the run lifecycle.
    core = events[:-1] if confirmation_valid and confirmation_events else events

    final_events = [event for event in core if event['phase'] in FINAL_PHASES]
    # A run may legitimately end at a pause (needs_input / needs_confirmation)
    # with no final event at all; it must still have exactly one terminal event
    # at the end, and that event must match the reported status. Trailing
    # checkpoint metadata (graph_pause) is allowed after it.
    add('single_terminal_event', len(final_events) <= 1,
        f'final_count={len(final_events)}')
    significant = [event for event in core
                   if event['phase'] not in TRAILING_META_PHASES]
    last_significant = significant[-1] if significant else None
    last_is_terminal = bool(last_significant) and last_significant['phase'] in TERMINAL_PHASES
    add('terminal_event_is_last', last_is_terminal,
        '' if last_is_terminal else f'last={last_significant["phase"] if last_significant else "none"}')
    add('terminal_event_matches_status',
        last_is_terminal and last_significant['phase'] == terminal,
        f'last={last_significant["phase"] if last_significant else "none"} status={terminal}')
    terminal_events = [event for event in core if event['phase'] in TERMINAL_PHASES]
    if terminal_events:
        index = core.index(terminal_events[-1])
        trailing = core[index + 1:]
        add('no_events_after_terminal',
            all(event['phase'] in TRAILING_META_PHASES for event in trailing),
            f'trailing={[event["phase"] for event in trailing]}')
    else:
        add('no_events_after_terminal', False, 'no terminal event')

    calls = [event for event in events if event['phase'] == 'tool_call']
    allowed_tools = set(expect.get('allowedTools', []))
    bad_tools = sorted({str(_detail(event).get('tool'))
                        for event in calls if _detail(event).get('tool') not in allowed_tools})
    add('tool_allowlist', not bad_tools, ','.join(bad_tools))

    allowed_places = set(expect.get('allowedPlaceIds', []))
    used_places = [place for event in calls
                   for place in _places_in_args(_detail(event).get('args'))]
    bad_places = sorted({place for place in used_places if place not in allowed_places})
    add('place_allowlist', not bad_places, ','.join(bad_places))

    signatures = [json.dumps({'tool': _detail(event).get('tool'),
                              'args': _detail(event).get('args')},
                             sort_keys=True, ensure_ascii=False) for event in calls]
    duplicates = [key for key, count in Counter(signatures).items() if count > 1]
    add('no_duplicate_tool_call', not duplicates, f'duplicates={len(duplicates)}')

    steps = [_detail(event).get('step') for event in calls
             if isinstance(_detail(event).get('step'), int)]
    max_calls = expect.get('maxToolCalls', 6)
    max_steps = expect.get('maxSteps', 6)
    within = len(calls) <= max_calls and (not steps or max(steps) <= max_steps)
    add('step_budget', within, f'calls={len(calls)}/{max_calls} max_step={max(steps) if steps else 0}/{max_steps}')

    written = bool(result.get('written'))
    add('no_write_on_non_completed_terminal', terminal == 'completed' or not written,
        f'terminal={terminal} written={written}')
    if written:
        # A terminal `completed` event is NOT a confirmation. Evidence is
        # either an authoritative `confirmed` event written by the confirm
        # endpoint, or an explicit confirmation object supplied by the capture
        # script from the real `/planner/confirm` response.
        confirmation = result.get('confirmation')
        explicit_event = any(event['phase'] in CONFIRMATION_PHASES for event in events)
        add('write_requires_confirmation', bool(confirmation) or explicit_event,
            'no confirmation evidence; a completed event does not prove a confirmed write')
    else:
        add('write_requires_confirmation', True, '')

    propose_index = next((i for i, event in enumerate(events)
                          if event['phase'] == 'tool_call'
                          and _detail(event).get('tool') == 'propose_plan'), None)
    if propose_index is not None and expect.get('requireValidationBeforeProposal'):
        prior = events[:propose_index]
        verification_tools = {'route_day', 'validate_plan'}
        validated = any(
            _detail(call).get('tool') in verification_tools
            and any(
                result_event['phase'] == 'tool_result'
                and result_event['status'] == 'ok'
                and _detail(result_event).get('step') == _detail(call).get('step')
                for result_event in prior
            )
            for call in prior if call['phase'] == 'tool_call'
        )
        add('validated_before_proposal', validated,
            'proposal without a successful route_day/validate_plan result')
    else:
        add('validated_before_proposal', True, '')

    # Conversation/intake-stage guards. These are no-ops for runs that never
    # issued MCP calls or searches, so the fixed suite stays comparable.
    mcp = [event for event in events if event['phase'] == 'mcp_call']
    if mcp:
        allowed_mcp = set(expect.get('allowedMcpTools') or DEFAULT_MCP_TOOLS)
        bad_mcp = sorted({str(_detail(event).get('tool')) for event in mcp
                          if _detail(event).get('tool') not in allowed_mcp})
        add('mcp_tool_allowlist', not bad_mcp, ','.join(bad_mcp))
        failed_mcp = [event for event in mcp
                      if _detail(event).get('ok') is False or event['status'] == 'error']
        add('mcp_call_results_ok', not failed_mcp, f'failed={len(failed_mcp)}')
    else:
        add('mcp_tool_allowlist', True, 'no mcp_call in this run')
        add('mcp_call_results_ok', True, 'no mcp_call in this run')

    searches = [event for event in events if event['phase'] == 'place_search']
    refines = [event for event in events if event['phase'] == 'search_refine']
    max_searches = expect.get('maxSearches', DEFAULT_MAX_SEARCHES)
    max_refines = expect.get('maxSearchRefineRounds', DEFAULT_MAX_REFINE_ROUNDS)
    add('search_budget', len(searches) <= max_searches and len(refines) <= max_refines,
        f'searches={len(searches)}/{max_searches} refines={len(refines)}/{max_refines}')
    queries = [str(_detail(event).get('query')) for event in searches
               if isinstance(_detail(event).get('query'), str)]
    add('no_duplicate_search_query', len(queries) == len(set(queries)),
        f'duplicates={len(queries) - len(set(queries))}')

    last_terminal_index = next((i for i in range(len(core) - 1, -1, -1)
                                if core[i]['phase'] in TERMINAL_PHASES), None)
    trailing_pauses = ([event for event in core[last_terminal_index + 1:]
                        if event['phase'] in TRAILING_META_PHASES]
                       if last_terminal_index is not None else [])
    add('trailing_pause_matches_status',
        all(_detail(event).get('status') == terminal for event in trailing_pauses),
        f'trailing_pauses={len(trailing_pauses)}')

    return checks


def _outcome_check(case: dict) -> tuple[bool, list[str]]:
    """Independent, simplified plan oracle. Does not call planner validators."""
    expect = case['expect']
    plan = case['result'].get('plan')
    reasons: list[str] = []
    if expect.get('expectProposal'):
        if not isinstance(plan, dict) or not _places_in_plan(plan):
            reasons.append('missing_proposal')
        else:
            places = _places_in_plan(plan)
            missing = [place for place in expect.get('requiredPlaceIds', []) if place not in places]
            if missing:
                reasons.append('missing_required:' + ','.join(missing))
            unknown = sorted({place for place in places
                              if place not in set(expect.get('allowedPlaceIds', []))})
            if unknown:
                reasons.append('unknown_place:' + ','.join(unknown))
            if expect.get('days') and len(plan.get('days', [])) != expect['days']:
                reasons.append('day_count')
    elif plan:
        reasons.append('unexpected_proposal')
    return (not reasons), reasons


def evaluate_trajectory(case: dict) -> dict:
    checks = _process_checks(case)
    process_pass = all(check['passed'] for check in checks)
    outcome_pass, reasons = _outcome_check(case)
    if process_pass and outcome_pass:
        verdict = 'both_pass'
    elif outcome_pass:
        verdict = 'result_only'
    elif process_pass:
        verdict = 'process_only'
    else:
        verdict = 'both_fail'
    return {
        'id': case['id'],
        'title': case['title'],
        'split': case['split'],
        'processPass': process_pass,
        'outcomePass': outcome_pass,
        'verdict': verdict,
        'passed': process_pass and outcome_pass,
        'failedChecks': [check['name'] for check in checks if not check['passed']],
        'outcomeReasons': reasons,
        'checks': checks,
    }


def evaluate_suite(cases: list[dict], labeled: bool = True) -> dict:
    """labeled=True checks the evaluator against human labels; labeled=False
    measures unlabelled, real runs without pretending they were judged."""
    if not cases:
        raise ValueError('empty trajectory suite')
    rows = [evaluate_trajectory(case) for case in cases]
    total = len(rows)
    quadrants = Counter(row['verdict'] for row in rows)
    process_pass = sum(row['processPass'] for row in rows)
    outcome_pass = sum(row['outcomePass'] for row in rows)
    proposals = sum(1 for case in cases if case['result'].get('plan'))
    # Every non-success terminal state is a refusal: step/guard exhaustion
    # ('incomplete') and user cancellation ('cancelled') must count too, or
    # refusalRate is understated and anti-gaming checks can be gamed.
    refusals = sum(1 for case in cases
                   if case['result'].get('status') in FINAL_PHASES - {'completed'})
    process_rate = process_pass / total
    proposal_rate = proposals / total
    suspected = process_rate >= 0.8 and proposal_rate < 0.5
    expected = [case.get('expectedPass') for case in cases]
    if labeled:
        false_accepts = [row['id'] for row, want in zip(rows, expected)
                         if row['passed'] and want is not True]
        false_rejects = [row['id'] for row, want in zip(rows, expected)
                         if not row['passed'] and want is True]
        agreement = sum(row['passed'] == bool(want) for row, want in zip(rows, expected))
        label_report = {
            'labeledPass': sum(want is True for want in expected),
            'negativeCases': sum(want is False for want in expected),
            'agreement': agreement,
            'evaluatorValid': agreement == total,
            'falseAccepts': false_accepts,
            'falseRejects': false_rejects,
        }
    else:
        label_report = {
            'labeledPass': None, 'negativeCases': None, 'agreement': None,
            'evaluatorValid': None, 'falseAccepts': [], 'falseRejects': [],
        }
    return {
        'suite': SUITE_VERSION,
        'mode': 'labeled' if labeled else 'observed',
        'total': total,
        'passed': sum(row['passed'] for row in rows),
        **label_report,
        'processPass': process_pass,
        'outcomePass': outcome_pass,
        'quadrants': {key: quadrants.get(key, 0) for key in
                      ('both_pass', 'result_only', 'process_only', 'both_fail')},
        'processPassRate': round(process_rate, 3),
        'outcomePassRate': round(outcome_pass / total, 3),
        'bothPassRate': round(sum(row['passed'] for row in rows) / total, 3),
        'proposalRate': round(proposal_rate, 3),
        'refusalRate': round(refusals / total, 3),
        'antiGaming': {
            'suspected': suspected,
            'reason': ('high process pass rate with low proposal rate: refusing tasks can inflate compliance'
                       if suspected else 'proposal rate is consistent with process pass rate'),
        },
        'note': 'Fixed synthetic trajectories. Not an AI accuracy or latency benchmark.',
        'cases': rows,
    }

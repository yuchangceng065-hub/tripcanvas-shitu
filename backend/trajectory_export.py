"""Export a persisted Agent run into a trajectory-evaluation case.

Observations come from `runs` / `run_events` / `trips`. Expectations MUST come
from the caller's pre-run inputs. Deriving the whitelist or required places
from the run's own result would make the oracle self-fulfilling: every run
would trivially pass its own assertions.

Usage:
    spec = {'terminal': 'completed', 'allowedPlaceIds': [...],
            'requiredPlaceIds': [...], 'expectProposal': True}
    case = export_case(run_id, spec)
    report = evaluate_suite([case], labeled=False)
"""
from __future__ import annotations

import json

from sqlalchemy import select

from backend.db import Run, RunEvent, SessionLocal, TripRow
from backend.runlog import decode_detail
from backend.trajectory_eval import (DEFAULT_MAX_REFINE_ROUNDS, DEFAULT_MAX_SEARCHES,
                                     DEFAULT_MCP_TOOLS)

DEFAULT_ALLOWED_TOOLS = [
    'route_day', 'validate_plan', 'propose_plan', 'compare_plans', 'repair_plan',
    'need_input', 'give_up', 'search_places', 'add_candidate',
]
REQUIRED_SPEC_KEYS = ('terminal', 'allowedPlaceIds', 'requiredPlaceIds', 'expectProposal')


def load_run(run_id: str) -> dict:
    """Read one run with its ordered events and its real write status."""
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f'run not found: {run_id}')
        events = list(session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        ))
        try:
            result = json.loads(run.result)
        except (TypeError, ValueError):
            result = {'status': 'unknown', 'corruptResult': True}
        validation = result.get('validation') if isinstance(result.get('validation'), dict) else {}
        trip = validation.get('trip') if isinstance(validation.get('trip'), dict) else {}
        candidate_trip_id = trip.get('id') or run.trip_id
        written = bool(candidate_trip_id and session.get(TripRow, candidate_trip_id))
        return {
            'run': {
                'id': run.id, 'owner': run.owner, 'trip_id': run.trip_id,
                'base_revision': run.base_revision, 'applied': run.applied,
                'created': run.created,
            },
            'result': result,
            'events': [{'seq': event.seq, 'phase': event.phase,
                        'status': event.status, 'detail': decode_detail(event.detail)}
                       for event in events],
            'written': written,
            'writeCheckedId': candidate_trip_id,
        }


def _plan_from_result(result: dict):
    """Normalise the produced trip into the shape the outcome oracle reads."""
    validation = result.get('validation')
    if not isinstance(validation, dict):
        return None
    trip = validation.get('trip')
    if not isinstance(trip, dict) or not trip.get('days'):
        return None
    return {'days': trip['days']}


def export_case(run_id: str, spec: dict) -> dict:
    missing = [key for key in REQUIRED_SPEC_KEYS if key not in spec]
    if missing:
        raise ValueError(f'{run_id}: spec must come from pre-run inputs; missing {missing}')
    data = load_run(run_id)
    result = data['result']
    expect = {
        'terminal': spec['terminal'],
        'allowedTools': list(spec.get('allowedTools', DEFAULT_ALLOWED_TOOLS)),
        'allowedPlaceIds': list(spec['allowedPlaceIds']),
        'requiredPlaceIds': list(spec['requiredPlaceIds']),
        'maxSteps': spec.get('maxSteps', 6),
        'maxToolCalls': spec.get('maxToolCalls', 6),
        'expectProposal': bool(spec['expectProposal']),
        'requireValidationBeforeProposal': bool(spec.get('requireValidationBeforeProposal', False)),
        'allowedMcpTools': list(spec.get('allowedMcpTools', DEFAULT_MCP_TOOLS)),
        'maxSearches': spec.get('maxSearches', DEFAULT_MAX_SEARCHES),
        'maxSearchRefineRounds': spec.get('maxSearchRefineRounds', DEFAULT_MAX_REFINE_ROUNDS),
    }
    if spec.get('days') is not None:
        expect['days'] = spec['days']
    return {
        'id': 'run:' + run_id[:12],
        'title': spec.get('title') or f'Observed run {run_id[:12]}',
        'split': 'observed',
        'expectedPass': None,
        'events': data['events'],
        'result': {
            'status': result.get('status'),
            'written': data['written'],
            'plan': _plan_from_result(result),
            'confirmation': spec.get('confirmation'),
        },
        'expect': expect,
        'provenance': {
            'runId': run_id,
            'kind': result.get('kind'),
            'owner': data['run']['owner'],
            'baseRevision': data['run']['base_revision'],
            'eventCount': len(data['events']),
            'writeCheckedId': data['writeCheckedId'],
            'expectationSource': 'caller-provided pre-run inputs (never derived from the run result)',
            'corruptResult': bool(result.get('corruptResult')),
            'stillRunning': result.get('status') in (None, 'running'),
        },
    }


def export_cases(spec_map: dict[str, dict]) -> list[dict]:
    """spec_map: {run_id: spec}. Defaults may be supplied under the '' key."""
    defaults = spec_map.get('', {})
    cases = []
    for run_id, spec in spec_map.items():
        if not run_id:
            continue
        merged = {**defaults, **spec}
        cases.append(export_case(run_id, merged))
    return cases


def export_case_from_payload(run_id: str, payload: dict, spec: dict) -> dict:
    """Build a case from GET /api/v1/runs/{id} without touching a local DB.

    `written` must be supplied by the caller (e.g. by checking GET /trips/{id});
    an API payload alone cannot prove whether a trip was persisted.
    """
    missing = [key for key in REQUIRED_SPEC_KEYS if key not in spec]
    if missing:
        raise ValueError(f'{run_id}: spec must come from pre-run inputs; missing {missing}')
    if 'written' not in spec:
        raise ValueError(f'{run_id}: written must be supplied by checking the trips API')
    expect = {
        'terminal': spec['terminal'],
        'allowedTools': list(spec.get('allowedTools', DEFAULT_ALLOWED_TOOLS)),
        'allowedPlaceIds': list(spec['allowedPlaceIds']),
        'requiredPlaceIds': list(spec['requiredPlaceIds']),
        'maxSteps': spec.get('maxSteps', 6),
        'maxToolCalls': spec.get('maxToolCalls', 6),
        'expectProposal': bool(spec['expectProposal']),
        'requireValidationBeforeProposal': bool(spec.get('requireValidationBeforeProposal', False)),
        'allowedMcpTools': list(spec.get('allowedMcpTools', DEFAULT_MCP_TOOLS)),
        'maxSearches': spec.get('maxSearches', DEFAULT_MAX_SEARCHES),
        'maxSearchRefineRounds': spec.get('maxSearchRefineRounds', DEFAULT_MAX_REFINE_ROUNDS),
    }
    if spec.get('days') is not None:
        expect['days'] = spec['days']
    events = []
    for event in payload.get('events', []):
        detail = event.get('detail')
        events.append({
            'seq': event.get('seq'),
            'phase': event.get('phase'),
            'status': event.get('status', 'ok'),
            'detail': detail if isinstance(detail, dict) else {},
        })
    validation = payload.get('validation')
    plan = None
    if isinstance(validation, dict) and isinstance(validation.get('trip'), dict):
        if validation['trip'].get('days'):
            plan = {'days': validation['trip']['days']}
    return {
        'id': 'run:' + run_id[:12],
        'title': spec.get('title') or f'Live run {run_id[:12]}',
        'split': 'observed',
        'expectedPass': None,
        'events': events,
        'result': {
            'status': payload.get('status'),
            'written': bool(spec['written']),
            'plan': plan,
            'confirmation': spec.get('confirmation'),
        },
        'expect': expect,
        'provenance': {
            'runId': run_id,
            'source': 'live-api-payload',
            'kind': payload.get('kind'),
            'eventCount': len(events),
            'expectationSource': 'caller-provided pre-run inputs',
            'writeStatusSource': 'GET /api/v1/trips/{id} (explicit)',
        },
    }


def spec_template() -> dict:
    return {
        'defaults': {
            'allowedTools': DEFAULT_ALLOWED_TOOLS,
            'maxSteps': 6,
            'maxToolCalls': 6,
            'requireValidationBeforeProposal': False,
        },
        'runs': {
            '<RUN_ID>': {
                'terminal': 'completed',
                'allowedPlaceIds': ['<confirmed candidate ids>'],
                'requiredPlaceIds': ['<must-visit ids>'],
                'expectProposal': True,
                'days': 1,
            },
        },
        'note': 'Fill expectations from the request that started the run, not from its result.',
    }

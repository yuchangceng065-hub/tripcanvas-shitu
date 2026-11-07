#!/usr/bin/env python3
"""Capture ONE real Agent run from the live API for trajectory evaluation.

Two modes:
  * intake (default) - one conversational turn, ending at the clarify stage.
  * full             - a structured Agent run carried through to a real
                       `/planner/confirm` save, so the capture contains
                       genuine "plan -> confirm -> write" evidence.

Both modes use a throwaway account and write a self-contained capture file for
`evaluate_trajectory.py --from-capture`. Expectations always come from the
PRE-RUN request; `written` is checked against GET /trips/{id}.

Confirmation evidence
---------------------
Deployed builds older than the local `runId`/`confirmed` feature do NOT emit a
`confirmed` run event. In `full` mode this script therefore derives evidence
from the authoritative `/planner/confirm` response (HTTP 200 + revision + a
200 write probe) and labels its source explicitly. `--confirmation-evidence`
lets a caller supply an equivalent object for other deployments. Evidence is
never invented from the run's own `completed` event.
"""
import argparse
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.trajectory_export import DEFAULT_ALLOWED_TOOLS  # noqa: E402

DEFAULT_API = 'https://tripcanvas-api.onrender.com/api/v1'
DEFAULT_TEXT = ('2026-11-14 去杭州一天，两个人，步行，想去曲院风荷看看，喜欢咖啡，'
                '17:30 前到杭州东站。')
DEFAULT_AGENT_BODY = {
    'title': '完整链路轨迹采集',
    'requirements': {'city': '杭州', 'startDate': '2026-11-14', 'days': 1,
                     'people': 2, 'mode': 'walk', 'start': 570, 'end': 1110,
                     'pace': 'relaxed', 'interests': []},
    'candidateIds': ['quyuan', 'museum'],
    'optionalIds': [],
}

# Expectations must come from the PRE-RUN request. For an intake-stage
# conversation there is no confirmed candidate set yet, so the allowlist is
# the places this request actually mentions - never the whole POI catalog.
MENTION_TO_PLACE = {
    '曲院风荷': 'quyuan', '浙江省博物馆': 'museum', '孤山': 'museum',
    '杭州东站': 'station', '雷峰塔': 'leifeng', '楼外楼': 'louwailou',
    '丝绸馆': 'silk', '浙江美术馆': 'art', '湖滨': 'hubin',
    '断桥': 'baidi', '苏堤': 'sudi', '柳浪闻莺': 'liulang',
}


def _ids(value: str | None) -> list[str]:
    return [item.strip() for item in (value or '').split(',') if item.strip()]


def _places_from_request(text: str, explicit: str | None):
    if explicit:
        return _ids(explicit)
    return sorted({place for mention, place in MENTION_TO_PLACE.items() if mention in text})


def _poll(client, api, run_id, headers, deadline):
    while True:
        response = client.get(api + '/runs/' + run_id, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') not in ('running', None):
            return payload
        if time.monotonic() > deadline:
            raise TimeoutError('run did not reach a terminal state')
        time.sleep(1.2)


def _drive_full_run(client, api, run_id, headers, deadline, max_replies):
    """Answer agent clarifications until a final state is reached."""
    handled = set()
    replies = []
    while True:
        payload = _poll(client, api, run_id, headers, deadline)
        status = payload.get('status')
        conversation = payload.get('conversation') or {}
        version = conversation.get('version')
        if (status == 'needs_input' and version is not None and version not in handled
                and len(replies) < max_replies):
            handled.add(version)
            body = {'version': version, 'requestId': uuid.uuid4().hex,
                    'text': '按你建议的顺序安排，必去地点全部保留，不要新增地点。'}
            reply = client.post(api + '/planner/conversations/' + run_id + '/reply',
                                headers=headers, json=body)
            replies.append({'version': version, 'status': reply.status_code})
            if reply.status_code != 200:
                break
            time.sleep(1.0)
            continue
        return payload, replies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default=DEFAULT_API)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('intake', 'full'), default='intake')
    parser.add_argument('--text', default=DEFAULT_TEXT)
    parser.add_argument('--expected-terminal', default='needs_input',
                        help=('intake mode: human expectation declared BEFORE the run. '
                              'Use needs_confirmation when the request already contains '
                              'every required field (the backend then skips the clarify '
                              'question and pauses at review instead of needs_input)'))
    parser.add_argument('--allowed-place-ids',
                        help='comma-separated place ids allowed by THIS request')
    parser.add_argument('--candidate-ids', default='quyuan,museum',
                        help='full mode: the confirmed candidate set sent to /planner/agent')
    parser.add_argument('--required-place-ids', default='quyuan,museum',
                        help='full mode: must-visit ids declared before the run')
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--max-replies', type=int, default=3)
    parser.add_argument('--confirmation-evidence', type=Path,
                        help='explicit JSON object used as confirmation evidence')
    parser.add_argument('--timeout', type=float, default=240)
    args = parser.parse_args()

    from backend.places import POIS
    full = args.mode == 'full'
    candidate_ids = _ids(args.candidate_ids)
    required_ids = _ids(args.required_place_ids) if full else []
    allowed_places = (_ids(args.allowed_place_ids) if full
                      else _places_from_request(args.text, args.allowed_place_ids))
    if full and not args.allowed_place_ids:
        parser.error('full mode requires an explicit --allowed-place-ids allowlist')
    if not allowed_places:
        parser.error('no allowed places: mention a place in --text or pass --allowed-place-ids')
    if set(allowed_places) == set(POIS.keys()):
        parser.error('allowedPlaceIds must not be the whole POI catalog')
    unknown = [place for place in allowed_places + candidate_ids + required_ids
               if place not in POIS]
    if unknown:
        parser.error('unknown place ids: ' + ','.join(sorted(set(unknown))))
    if not set(candidate_ids + required_ids) <= set(allowed_places):
        parser.error('candidate/required ids must be inside the declared allowlist')
    allowed_source = ('explicit --allowed-place-ids (pre-run confirmed candidate set + anchors)'
                      if full else
                      ('explicit --allowed-place-ids' if args.allowed_place_ids
                       else 'place mentions in the pre-run request text'))

    explicit_evidence = None
    if args.confirmation_evidence:
        explicit_evidence = json.loads(args.confirmation_evidence.read_text(encoding='utf-8'))
        if not isinstance(explicit_evidence, dict):
            parser.error('--confirmation-evidence must contain a JSON object')

    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    username = 'capture_' + uuid.uuid4().hex[:10]
    # Never commit a fixed credential: the throwaway account is discarded after
    # the capture, so a random password is strictly safer and needs no config.
    password = os.environ.get('CAPTURE_PASSWORD') or secrets.token_urlsafe(24)

    with httpx.Client(proxy=proxy, trust_env=True, timeout=90) as client:
        auth = client.post(args.api + '/auth/register',
                           json={'username': username, 'password': password})
        auth.raise_for_status()
        token = auth.json().get('access_token')
        headers = {'Authorization': 'Bearer ' + token} if token else {}

        if full:
            body = dict(DEFAULT_AGENT_BODY)
            body['candidateIds'] = candidate_ids
            body['requirements'] = {**DEFAULT_AGENT_BODY['requirements'], 'days': args.days}
            started = client.post(args.api + '/planner/agent', headers=headers, json=body)
        else:
            body = {'title': '轨迹采集', 'text': args.text}
            started = client.post(args.api + '/planner/conversations',
                                  headers=headers, json=body)
        started.raise_for_status()
        run_id = started.json().get('runId')
        if not run_id:
            raise RuntimeError('no runId returned: ' + started.text[:200])

        deadline = time.monotonic() + args.timeout
        replies = []
        if full:
            payload, replies = _drive_full_run(client, args.api, run_id, headers,
                                               deadline, args.max_replies)
        else:
            payload = _poll(client, args.api, run_id, headers, deadline)

        trip_id = None
        validation = payload.get('validation')
        if isinstance(validation, dict) and isinstance(validation.get('trip'), dict):
            trip_id = validation['trip'].get('id')

        confirm_response = None
        confirmation = explicit_evidence
        if full and payload.get('status') == 'completed' and trip_id:
            trip = validation['trip']
            base_revision = max(0, int(trip.get('revision', 0)) - 1)
            confirmed = client.post(args.api + '/planner/confirm', headers=headers,
                                    json={'trip': trip, 'baseRevision': base_revision})
            confirm_response = {'httpStatus': confirmed.status_code,
                                'body': confirmed.json() if confirmed.headers.get(
                                    'content-type', '').startswith('application/json') else None}
            if confirmed.status_code == 200 and confirmation is None:
                confirmation = {
                    'source': 'POST /api/v1/planner/confirm (live API response)',
                    'httpStatus': confirmed.status_code,
                    'tripId': trip_id,
                    'revision': confirm_response['body'].get('revision'),
                    'idempotent': confirm_response['body'].get('idempotent'),
                    'writeProbe': 'GET /api/v1/trips/{id}',
                    'observedAt': datetime.now(timezone.utc).isoformat(),
                    'note': ('Deployed build does not emit a confirmed run event; '
                             'evidence comes from the authoritative confirm response, '
                             'never from the run\'s completed event.'),
                }

        written = False
        if trip_id:
            probe = client.get(args.api + '/trips/' + trip_id, headers=headers)
            written = probe.status_code == 200
        if confirmation is not None:
            confirmation = {**confirmation, 'writeProbeStatus': 200 if written else None}
            if explicit_evidence is not None:
                confirmation['source'] = 'caller-provided --confirmation-evidence'

        spec = {
            'terminal': 'completed' if full else args.expected_terminal,
            'allowedTools': DEFAULT_ALLOWED_TOOLS,
            'allowedPlaceIds': allowed_places,
            'allowedPlaceIdsSource': allowed_source,
            'requiredPlaceIds': required_ids,
            'expectProposal': full,
            'maxSteps': 6,
            'maxToolCalls': 6,
            'requireValidationBeforeProposal': full,
            'written': written,
            'confirmation': confirmation,
            'title': '线上完整规划→确认→保存运行' if full else '线上真实对话运行',
        }
        if full:
            spec['days'] = args.days

        capture = {
            'captureVersion': 'live-run/v1',
            'mode': args.mode,
            'apiBase': args.api,
            'capturedAt': datetime.now(timezone.utc).isoformat(),
            'request': body,
            'account': username,
            'healthAtCapture': client.get(args.api + '/health').json(),
            'allowedPlaceIds': allowed_places,
            'allowedPlaceIdsSource': allowed_source,
            'poiCatalogSize': len(POIS),
            'clarificationReplies': replies,
            'confirmResponse': confirm_response,
            'confirmationSource': (confirmation or {}).get('source'),
            'runs': [{
                'runId': run_id,
                'observedStatus': payload.get('status'),
                'observedEventCount': len(payload.get('events', [])),
                'observedPhases': [event.get('phase') for event in payload.get('events', [])],
                'tripId': trip_id,
                'written': written,
                'spec': spec,
                'payload': payload,
            }],
            'sideEffects': ('one QA account, one run and its events, one saved trip '
                            'and 2-6 Groq free-tier calls' if full else
                            'one QA account, one run and its events, 1-2 Groq free-tier calls; no trip created'),
            'cleanup': 'preferences deleted, session revoked',
        }

        client.delete(args.api + '/preferences', headers=headers)
        client.post(args.api + '/auth/logout', headers=headers, json={})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(capture, ensure_ascii=False, indent=2), encoding='utf-8')
    run = capture['runs'][0]
    print(json.dumps({
        'capture': str(args.output), 'mode': args.mode, 'runId': run['runId'],
        'observedStatus': run['observedStatus'],
        'events': run['observedEventCount'], 'phases': run['observedPhases'],
        'written': run['written'], 'expectedTerminal': spec['terminal'],
        'confirmationSource': capture['confirmationSource'],
        'clarificationReplies': replies,
        'health': capture['healthAtCapture'],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

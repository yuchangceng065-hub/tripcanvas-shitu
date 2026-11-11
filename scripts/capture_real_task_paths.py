#!/usr/bin/env python3
"""Record three observed Agent paths with one isolated QA account."""

import argparse
import json
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_real_task_suite import declared_spec  # noqa: E402
from scripts.capture_live_run import DEFAULT_AGENT_BODY, DEFAULT_API, _poll  # noqa: E402


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def conversation_body():
    return {
        'title': 'QA · 需求确认',
        'text': '2026-11-14 曲院风荷玩一天，09:30开始，18:30结束。人数和交通方式暂未确定。',
    }


def clarify(client, deadline):
    body = conversation_body()
    run_id = request(client, 'POST', '/planner/conversations', json=body)['runId']
    path = '/runs/' + run_id
    paused = _poll(client, str(client.base_url).rstrip('/'), run_id, {}, deadline)
    if paused['status'] != 'needs_input':
        return body, paused, {}
    conversation = paused['conversation']
    choices = {}
    for mention in conversation['mentions']:
        match = next((place['id'] for place in mention['candidates']
                      if place['id'] in ('quyuan', 'amap:B023B0253E')), None)
        if match:
            choices[mention['key']] = match
    reply = {
        'text': '两个人同行，步行，只去曲院风荷。不需要返程时间，也不用安排起点。',
        'choices': choices,
        'version': conversation['version'], 'requestId': uuid.uuid4().hex,
    }
    continued = request(client, 'POST', '/planner/conversations/' + run_id + '/reply', json=reply)
    result = _poll(client, str(client.base_url).rstrip('/'), run_id, {}, deadline)
    return body, result, {'pausedStatus': paused['status'],
                          'sameRun': continued['runId'] == run_id == result.get('runId'),
                          'replyVersion': reply['version'], 'resumedVersion': continued['version'],
                          'readBack': request(client, 'GET', path)['status']}


def reconnect(client, deadline):
    body = conversation_body()
    run_id = request(client, 'POST', '/planner/conversations', json=body)['runId']
    path = '/runs/' + run_id + '/events'
    with client.stream('GET', path) as response:
        response.raise_for_status()
        cursor = next(int(line[4:]) for line in response.iter_lines() if line.startswith('id: '))
    result = _poll(client, str(client.base_url).rstrip('/'), run_id, {}, deadline)
    response = client.get(path, headers={'Last-Event-ID': str(cursor)})
    response.raise_for_status()
    replayed = [int(line[4:]) for line in response.text.splitlines() if line.startswith('id: ')]
    return body, result, {'cursor': cursor, 'replayedIds': replayed}


def infeasible(client, deadline):
    body = {
        **DEFAULT_AGENT_BODY, 'title': 'QA · 必去活动超出时段',
        'requirements': {**DEFAULT_AGENT_BODY['requirements'], 'start': 570, 'end': 585},
    }
    run_id = request(client, 'POST', '/planner/agent', json=body)['runId']
    result = _poll(client, str(client.base_url).rstrip('/'), run_id, {}, deadline)
    return body, result, {}


TASKS = {
    'clarification_resume': ('needs_confirmation', clarify),
    'reconnect_recovery': ('needs_input', reconnect),
    'infeasible_constraint': ('gave_up', infeasible),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default=DEFAULT_API)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--category', choices=TASKS, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.api, timeout=90) as client:
        health = request(client, 'GET', '/health')
        auth = request(client, 'POST', '/auth/register', json={
            'username': 'path_qa_' + uuid.uuid4().hex[:10],
            'password': secrets.token_urlsafe(24),
        })
        client.headers['Authorization'] = 'Bearer ' + auth['access_token']
        try:
            category = args.category
            terminal, run = TASKS[category]
            spec = declared_spec(category)
            spec.update(terminal=terminal, allowedPlaceIds=['quyuan', 'museum', 'amap:B023B0253E'],
                        allowedPlaceIdsSource='explicit QA candidates fixed before execution')
            declared = datetime.now(timezone.utc).isoformat()
            before = request(client, 'GET', '/trips')
            body, result, evidence = run(client, time.monotonic() + 120)
            after = request(client, 'GET', '/trips')
            run_id = result.get('runId') or result.get('id')
            capture = {
                'captureVersion': 'live-run/v1',
                'source': 'live API payload, desensitized',
                'declaredAt': declared,
                'capturedAt': datetime.now(timezone.utc).isoformat(),
                'healthAtCapture': health,
                'request': body,
                'runs': [{'runId': run_id, 'payload': result, 'spec': spec,
                          'written': before != after, 'evidence': evidence}],
            }
            (args.output_dir / f'{category}.json').write_text(
                json.dumps(capture, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
            )
            print(category, result['status'], evidence, flush=True)
        finally:
            client.post('/auth/logout', json={})


if __name__ == '__main__':
    main()

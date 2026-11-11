#!/usr/bin/env python3
"""Exercise TripCanvas write contention against a disposable loopback PostgreSQL."""

import argparse
import json
import logging
import math
import os
import platform
import secrets
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event, Thread
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def timing(values):
    ordered = sorted(values)
    return {
        'p50Ms': round(ordered[math.ceil(len(ordered) * .5) - 1], 3),
        'p95Ms': round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
        'maxMs': round(ordered[-1], 3),
    }


@contextmanager
def monitor_locks(connection):
    stop = Event()
    ready = Event()
    samples = []

    def observe():
        while not stop.is_set():
            samples.append(connection.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND application_name = 'tripcanvas' "
                "AND wait_event_type = 'Lock'"
            ).fetchone()[0])
            ready.set()
            stop.wait(.01)

    observer = Thread(target=observe)
    observer.start()
    try:
        if not ready.wait(5):
            raise RuntimeError('Lock sampler did not start')
        yield samples
    finally:
        stop.set()
        observer.join(5)


def measure(worker_factory, workers, operations, connection):
    barrier = Barrier(workers + 1, timeout=20)

    def work(index):
        operation = worker_factory()
        barrier.wait()
        results = []
        for _ in range(index, operations, workers):
            started = time.perf_counter()
            try:
                result = operation()
            except Exception as error:
                result = type(error).__name__
            results.append((result, (time.perf_counter() - started) * 1000))
        return results

    with monitor_locks(connection) as samples, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, index) for index in range(workers)]
        started = time.perf_counter()
        barrier.wait()
        results = [result for future in futures for result in future.result()]
        elapsed = time.perf_counter() - started
    statuses = Counter(value for value, _ in results)
    return {
        'workers': workers, 'operations': len(results),
        'throughputPerSecond': round(len(results) / elapsed, 1),
        'latency': timing([duration for _, duration in results]),
        'statuses': dict(sorted(statuses.items())),
        'lockSamples': {
            'intervalMs': 10, 'total': len(samples),
            'withWaiters': sum(sample > 0 for sample in samples),
            'maxWaiters': max(samples, default=0),
        },
    }


def run_benchmark(connection, workers, operations, repeats):
    from backend.app import app
    from backend.runlog import RunRecorder, events_for
    from backend.schemas import Trip
    from backend.store import proposal_key

    results = []
    with TestClient(app, raise_server_exceptions=False) as client, patch('backend.routers.planner.throttle'):
        auth = client.post('/api/v1/auth/register', json={
            'username': 'bench_' + uuid.uuid4().hex[:12], 'password': secrets.token_urlsafe(24),
        })
        auth.raise_for_status()
        headers = {'Authorization': 'Bearer ' + auth.json()['access_token']}
        from backend.db import SessionLocal, User

        with SessionLocal() as session:
            owner = session.query(User).filter_by(username=auth.json()['username']).one().id
        for workers_count in workers:
            for repeat in range(1, repeats + 1):
                trip = {
                    'id': str(uuid.uuid4()), 'title': 'PostgreSQL benchmark', 'city': '杭州',
                    'people': 2, 'mode': 'walk', 'revision': 1,
                    'days': [{'date': '2026-11-21', 'start': 540, 'end': 1050,
                              'activities': [{'id': 'a', 'placeId': 'quyuan', 'duration': 90, 'lock': 'none'}]}],
                }
                run = RunRecorder(owner, '', 0, 'agent')
                run.complete({'confirmationHashes': [proposal_key(Trip.model_validate(trip))]})
                body = {'trip': trip, 'baseRevision': 0, 'runId': run.id}

                def confirmation_worker():
                    # Each worker owns its ASGI request portal. The app lifespan
                    # and database engine belong to the enclosing client.
                    worker = TestClient(app, raise_server_exceptions=False)

                    def confirm():
                        response = worker.post('/api/v1/planner/confirm', headers=headers, json=body)
                        if response.status_code != 200:
                            return str(response.status_code)
                        return '200_replay' if response.json()['idempotent'] else '200_saved'

                    return confirm

                confirmation = measure(confirmation_worker, workers_count, operations, connection)
                path = '/api/v1/trips/' + trip['id']
                versions = client.get(path + '/versions', headers=headers)
                count = len(versions.json()) if versions.status_code == 200 else 0
                confirmed = sum(event.phase == 'confirmed' for event in events_for(run.id))
                correct = confirmation['statuses'] == {'200_saved': 1, '200_replay': operations - 1}
                confirmation.update(workload='confirm_same_new_trip', repeat=repeat,
                                    versions=count, confirmedEvents=confirmed,
                                    integrityPassed=count == confirmed == 1,
                                    passed=correct and count == confirmed == 1,
                                    errorRate=round(sum(count for status, count in confirmation['statuses'].items()
                                                        if not status.startswith('200_')) / operations, 4))
                results.append(confirmation)
                client.delete(path, headers=headers)

                event_run = RunRecorder(owner, '', 0, 'agent')

                def event_worker():
                    recorder = RunRecorder.restore(event_run.id)
                    return lambda: 'written' if recorder.event('benchmark') is not None else 'dropped'

                appended = measure(event_worker, workers_count, operations, connection)
                sequences = [event.seq for event in events_for(event_run.id)]
                integrity = sequences == list(range(1, operations + 1))
                appended.update(workload='append_events_same_run', repeat=repeat,
                                eventCount=len(sequences), uniqueSequences=len(set(sequences)),
                                integrityPassed=integrity, passed=integrity and appended['statuses'] == {'written': operations},
                                errorRate=round(1 - appended['statuses'].get('written', 0) / operations, 4))
                results.append(appended)
        client.post('/api/v1/auth/logout', headers=headers, json={})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', nargs='+', type=int, default=[1, 4, 12, 24])
    parser.add_argument('--operations', type=int, default=72)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.operations <= 500 or not 1 <= args.repeats <= 10 or not all(
        1 <= workers <= min(args.operations, 48) for workers in args.workers
    ):
        parser.error('Expected 2–500 operations, 1–10 repeats and up to 48 workers')
    supplied = os.environ.get('TRIPCANVAS_BENCHMARK_DATABASE_URL', '')
    url = make_url(supplied) if supplied else None
    if not url or url.get_backend_name() != 'postgresql' or url.host not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Set TRIPCANVAS_BENCHMARK_DATABASE_URL to a disposable loopback PostgreSQL instance')
    os.environ.update(DATABASE_URL=supplied, PLANNER_MODE='rules', MODEL_API_KEY='', GROQ_API_KEY='',
                      AMAP_WEB_KEY='', AMAP_JS_KEY='', AMAP_JS_SECURITY_CODE='')
    logging.basicConfig(level=logging.WARNING)
    connect_url = url.set(drivername='postgresql').render_as_string(hide_password=False)
    with psycopg.connect(connect_url, autocommit=True, application_name='tripcanvas-benchmark-observer') as connection:
        report = {
            'environment': {'postgres': connection.info.server_version, 'platform': platform.platform(),
                            'logicalCpus': os.cpu_count(), 'python': platform.python_version(),
                            'gitCommit': os.getenv('GITHUB_SHA'),
                            'dbPoolSize': 2, 'dbPoolOverflow': 2,
                            'transport': 'in-process ASGI; external providers disabled; confirm throttle bypassed'},
            'results': run_benchmark(connection, args.workers, args.operations, args.repeats),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return int(not all(row['passed'] for row in report['results']))


if __name__ == '__main__':
    sys.exit(main())

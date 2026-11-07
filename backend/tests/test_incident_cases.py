"""Accident-class regressions and governance contracts.

Passing tests lock in protections under concurrency, stale writers, sealed
terminals and oversized context. Fencing and atomic sequence allocation are
implemented in `backend/runlog.py`; these are ordinary passing tests, not xfails.
"""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.app import app, attempts
    attempts.clear()
    with TestClient(app) as c:
        yield c


# --- Billing semantics: unknown usage is never zero -------------------------

def test_unknown_usage_is_never_reported_as_zero_cost():
    from backend.model_governance import usage_report
    from backend.model_metrics import empty_metrics, record_usage
    price = {'promptPerMillion': 1.0, 'completionPerMillion': 4.0,
             'version': 'test-v1', 'currency': 'USD'}

    known = empty_metrics()
    record_usage(known, {'prompt_tokens': 1000, 'completion_tokens': 500, 'total_tokens': 1500})
    report = usage_report(known, price)
    assert report['costKnown'] and report['cost'] == pytest.approx(0.003)

    for missing in (None, {}, {'totalTokens': 0}):
        assert usage_report(missing, price)['cost'] is None
        assert usage_report(missing, price)['reason'] == 'provider_usage_unknown'

    unknown = empty_metrics()
    record_usage(unknown, None)
    assert usage_report(unknown, price)['costKnown'] is False
    # A genuinely observed zero is still reported as known, not unknown.
    assert usage_report({'totalTokens': 0, 'tokensComplete': True}, price)['costKnown'] is True


def test_usage_snapshot_is_auditable_and_price_versioned():
    from backend.model_governance import usage_report, usage_snapshot
    metrics = {'promptTokens': 10, 'completionTokens': 5, 'totalTokens': 15,
               'providerRequests': 1, 'providerSeconds': 1.2, 'tokensComplete': True}
    v1 = {'promptPerMillion': 1.0, 'completionPerMillion': 2.0, 'version': 'v1', 'currency': 'USD'}
    v2 = {'promptPerMillion': 9.0, 'completionPerMillion': 9.0, 'version': 'v2', 'currency': 'USD'}
    snap = usage_snapshot(metrics, run_id='r1', owner_id='u1', model='gpt-oss-20b', price=v1)
    assert snap['schemaVersion'] == 'usage-report/v1'
    assert snap['report']['priceTableVersion'] == 'v1' and snap['report']['costKnown']
    assert usage_report(metrics, v2)['priceTableVersion'] == 'v2'
    assert snap['report']['priceTableVersion'] == 'v1'  # historical snapshot is never rewritten


# --- Protocol semantics: native SSE stays, AG-UI is a view ------------------

def test_agui_adapter_covers_every_internal_phase():
    from backend.agui_events import PHASE_MAP, capabilities, to_agui_event
    assert capabilities()['transport'] == 'text/event-stream'
    assert capabilities()['nativeFormat'] == 'sse'
    for phase, expected in PHASE_MAP.items():
        event = to_agui_event(phase, 'ok', {'tool': 'search'}, 1, 'run-1')
        assert event['type'] == expected
        assert event['protocol'] == 'ag-ui/1.0'
        assert event['seq'] == 1
    assert to_agui_event('never_seen', 'ok', {}, 2, 'run-1')['type'] == capabilities()['unmappedFallback']


def test_agui_frame_keeps_cursor_and_strips_secrets():
    from backend.agui_events import to_agui_frame
    detail = {'tool': 'search', 'api_key': 'super-secret',
              'prompt': 'raw prompt', 'nested': {'a': 'b'}}
    frame = to_agui_frame('tool_call', 'ok', detail, 7, 'run-1')
    assert frame.startswith('id: 7\n')
    assert 'event: TOOL_CALL_START' in frame
    assert 'super-secret' not in frame and 'raw prompt' not in frame
    data = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))
    assert data['seq'] == 7 and data['toolCallId'] == 'run-1:7'


# --- Accident class: concurrency, context budget, cursor --------------------

def test_parallel_daily_quota_never_exceeds_limit(client):
    from backend.app import reserve_user_daily
    uid = 'race-' + uuid.uuid4().hex[:6]

    def attempt(_):
        try:
            reserve_user_daily(uid, 'plans', 3)
            return 'ok'
        except HTTPException as exc:
            return exc.status_code
        except Exception as exc:  # SQLite may serialize or refuse a competing writer
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(6)))
    # Serialization failures are acceptable; over-reservation is not.
    assert outcomes.count('ok') <= 3, outcomes


def test_event_detail_budget_is_bounded_and_history_degrades_safely():
    from backend.runlog import decode_detail, encode_detail
    huge = {'text': 'x' * 20000,
            'nested': {'level' + str(i): 'y' * 500 for i in range(80)}}
    encoded = encode_detail(huge)
    assert len(encoded) <= 4000
    assert isinstance(decode_detail(encoded), dict)
    assert decode_detail('{broken')['truncated'] is True


def test_cursor_resume_is_idempotent_and_monotonic(client):
    from backend.runlog import RunRecorder, events_for
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], 'trip', 1, 'agent')
    for phase in ('model', 'tool_call', 'tool_result', 'completed'):
        rec.event(phase)
    assert [e.seq for e in events_for(rec.id)] == [1, 2, 3, 4]
    assert [e.seq for e in events_for(rec.id, 2)] == [3, 4]
    assert [e.seq for e in events_for(rec.id, 2)] == [3, 4]  # replay creates nothing


# --- Fencing, atomic seq and authoritative write confirmation ---------------

def test_stale_writer_cannot_reopen_finished_run(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], 'trip', 1, 'agent')
    rec.complete({'ok': True})
    stale = RunRecorder.restore(rec.id)  # an old background task after a restart
    assert stale.restart() is False
    assert stale.event('resumed') is None  # sealed runs accept no Agent events
    with SessionLocal() as session:
        row = session.get(Run, rec.id)
        assert json.loads(row.result)['status'] == 'completed', 'stale writer reopened a finished run'
        assert row.generation == 0


def test_parallel_recorders_do_not_reuse_sequence(client):
    from backend.runlog import RunRecorder, events_for
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], 'trip', 1, 'agent')
    first = RunRecorder.restore(rec.id)
    second = RunRecorder.restore(rec.id)
    assert first.event('model') == 1
    assert second.event('tool_call') == 2
    seqs = [e.seq for e in events_for(rec.id)]
    assert len(seqs) == len(set(seqs)), f'seq collision drops SSE events: {seqs}'


def test_resume_bumps_generation_and_fences_the_previous_activation(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], 'trip', 1, 'agent')
    rec.finish('needs_input', {'question': '哪天出发？'})
    stale = RunRecorder.restore(rec.id)  # activation 0, still holding the old token
    resumed = RunRecorder.restore(rec.id)
    assert resumed.restart() is True
    assert resumed.generation == 1
    # The old activation is fenced out of both events and result writes.
    assert stale.event('model') is None
    assert stale.patch({'modelUsage': {'totalTokens': 999}}) is False
    with SessionLocal() as session:
        row = session.get(Run, rec.id)
        assert row.generation == 1
        assert 'modelUsage' not in json.loads(row.result)


def test_confirm_is_an_authoritative_post_terminal_event(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    rec = RunRecorder('owner-' + uuid.uuid4().hex[:6], 'trip-1', 0, 'agent')
    rec.complete({'validation': {'trip': {'id': 'trip-1'}}})
    writer = RunRecorder.restore(rec.id)
    assert writer.confirm({'tripId': 'trip-1', 'revision': 1, 'source': 'planner/confirm'}) is True
    assert writer.confirm({'tripId': 'trip-1', 'revision': 1}) is True  # idempotent
    phases = [event.phase for event in events_for(rec.id)]
    assert phases == ['completed', 'confirmed'], phases
    with SessionLocal() as session:
        assert session.get(Run, rec.id).applied == 1


def test_agui_view_is_opt_in_and_native_stream_is_unchanged(client):
    from sqlalchemy import select
    from backend.db import User, SessionLocal
    from backend.runlog import RunRecorder

    username = 'agui_' + uuid.uuid4().hex[:6]
    assert client.post('/api/v1/auth/register',
                       json={'username': username, 'password': 'correct-horse-824!'}).status_code == 200
    with SessionLocal() as session:
        owner = session.scalar(select(User).where(User.username == username)).id
    rec = RunRecorder(owner, 'trip', 1, 'agent')
    rec.event('model', detail={'tool': 'search', 'api_key': 'must-not-leak'})
    rec.complete({'ok': True})

    native = client.get(f'/api/v1/runs/{rec.id}/events')
    adapted = client.get(f'/api/v1/runs/{rec.id}/events', params={'format': 'ag-ui'})
    assert native.status_code == 200 and adapted.status_code == 200
    assert 'event: model' in native.text and 'event: completed' in native.text
    assert 'event: STEP_STARTED' in adapted.text
    assert 'event: RUN_FINISHED' in adapted.text
    assert 'must-not-leak' not in adapted.text
    assert adapted.text.startswith('id: 1\n')


def test_agui_frame_never_leaks_nested_secrets():
    from backend.agui_events import to_agui_event, to_agui_frame
    detail = {'nested': {'token': 'super-secret', 'ok': True},
              'outer': {'inner': {'api_key': 'nested-key'}}, 'tool': 'search'}
    frame = to_agui_frame('tool_call', 'ok', detail, 1, 'r')
    assert 'super-secret' not in frame and 'nested-key' not in frame
    event = to_agui_event('tool_call', 'ok', detail, 1, 'r')
    assert event['detail']['nested'] == {'ok': True}


def test_agui_tool_call_and_result_share_a_stable_id():
    from backend.agui_events import capabilities, to_agui_event
    call = to_agui_event('tool_call', 'ok',
                         {'step': 2, 'tool': 'route_day', 'args': {'days': [['quyuan']]}}, 3, 'r')
    result = to_agui_event('tool_result', 'ok',
                           {'step': 2, 'ok': True, 'summary': 'ok'}, 5, 'r')
    assert call['toolCallId'] == result['toolCallId'] == 'r:step:2'
    assert call['args'] == {'days': [['quyuan']]}
    assert result['result']['ok'] is True
    assert capabilities()['compatibility'] == 'semantic-view'


def test_cost_requires_a_token_breakdown_and_a_configured_price(monkeypatch):
    from backend.model_governance import usage_report
    price = {'promptPerMillion': 1.0, 'completionPerMillion': 4.0,
             'version': 'test-v1', 'currency': 'USD'}
    only_total = {'totalTokens': 1000, 'tokensComplete': True}
    report = usage_report(only_total, price)
    assert report['cost'] is None and report['costKnown'] is False
    assert report['reason'] == 'usage_breakdown_unavailable'
    complete = {'promptTokens': 1000, 'completionTokens': 500,
                'totalTokens': 1500, 'tokensComplete': True}
    for key in ('PRICE_PROMPT_PER_MILLION', 'PRICE_COMPLETION_PER_MILLION'):
        monkeypatch.delenv(key, raising=False)
    unpriced = usage_report(complete)
    assert unpriced['cost'] is None and unpriced['reason'] == 'price_not_configured'


def _priced(monkeypatch, prompt='1.0', completion='4.0', version='v1'):
    monkeypatch.setenv('PRICE_PROMPT_PER_MILLION', prompt)
    monkeypatch.setenv('PRICE_COMPLETION_PER_MILLION', completion)
    monkeypatch.setenv('PRICE_TABLE_VERSION', version)
    return {'providerRequests': 1, 'promptTokens': 1000,
            'completionTokens': 500, 'totalTokens': 1500, 'tokensComplete': True}


def test_terminal_run_freezes_a_usage_snapshot(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    metrics = _priced(monkeypatch)
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    assert rec.complete({'modelUsage': metrics})
    with SessionLocal() as session:
        result = json.loads(session.get(Run, rec.id).result)
    snap = result['usageSnapshot']
    assert snap['schemaVersion'] == 'usage-report/v1' and snap['runId'] == rec.id
    assert snap['report']['costKnown'] is True
    assert snap['report']['cost'] == pytest.approx(0.003)
    assert snap['report']['priceTableVersion'] == 'v1'


def test_price_change_never_rewrites_a_terminal_usage_snapshot(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for, run_payload
    metrics = _priced(monkeypatch)
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    rec.complete({'modelUsage': metrics})
    with SessionLocal() as session:
        stored = json.loads(session.get(Run, rec.id).result)['usageSnapshot']
    _priced(monkeypatch, '99', '99', 'v2')
    with SessionLocal() as session:
        payload = run_payload(session.get(Run, rec.id), events_for(rec.id))
        persisted = json.loads(session.get(Run, rec.id).result)['usageSnapshot']
    assert payload['usageReportSource'] == 'terminal-snapshot'
    assert payload['usageReport']['cost'] == pytest.approx(0.003)
    assert payload['usageReport']['priceTableVersion'] == 'v1'
    assert persisted == stored


def test_unknown_usage_snapshot_stays_unknown(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    _priced(monkeypatch)
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    assert rec.complete({'modelUsage': {'totalTokens': 0, 'tokensComplete': False}})
    with SessionLocal() as session:
        report = json.loads(session.get(Run, rec.id).result)['usageSnapshot']['report']
    assert report['cost'] is None and report['costKnown'] is False
    assert report['reason'] == 'provider_usage_unknown'


def test_final_usage_patch_freezes_snapshot_once(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    metrics = _priced(monkeypatch)
    rec = RunRecorder('owner', 'trip', 0, 'conversation')
    assert rec.complete({'validation': {'trip': {'id': 'x'}}})
    with SessionLocal() as session:
        assert 'usageSnapshot' not in json.loads(session.get(Run, rec.id).result)
    # This mirrors schedule()'s finally block: usage arrives after the terminal.
    assert rec.patch({'modelUsage': metrics}) is True
    with SessionLocal() as session:
        snap = json.loads(session.get(Run, rec.id).result)['usageSnapshot']
    assert snap['report']['cost'] == pytest.approx(0.003)
    assert rec.patch({'engine': 'langgraph'}) is True
    assert rec.patch({'modelUsage': {'totalTokens': 999, 'tokensComplete': False}}) is True
    with SessionLocal() as session:
        result = json.loads(session.get(Run, rec.id).result)
    assert result['usageSnapshot'] == snap
    assert result['status'] == 'completed'


def test_retry_keeps_earlier_failure_snapshot_in_history(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder
    metrics = _priced(monkeypatch)
    rec = RunRecorder('owner', 'trip', 0, 'conversation')
    rec.fail('temporary')
    assert RunRecorder.restore(rec.id).patch({'modelUsage': metrics}) is True
    with SessionLocal() as session:
        first = json.loads(session.get(Run, rec.id).result)['usageSnapshot']
    resumed = RunRecorder.restore(rec.id)
    assert resumed.restart() is True
    _priced(monkeypatch, '2.0', '8.0', 'v2')
    assert resumed.complete({'modelUsage': metrics}) is True
    with SessionLocal() as session:
        result = json.loads(session.get(Run, rec.id).result)
    assert result['usageSnapshotHistory'] == [first]
    assert result['usageSnapshot']['report']['cost'] == pytest.approx(0.006)
    assert result['usageSnapshot']['report']['priceTableVersion'] == 'v2'


def test_run_payload_exposes_a_billing_view(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for, run_payload
    for key in ('PRICE_PROMPT_PER_MILLION', 'PRICE_COMPLETION_PER_MILLION'):
        monkeypatch.delenv(key, raising=False)
    rec = RunRecorder('u-' + uuid.uuid4().hex[:6], 't', 1, 'agent')
    rec.complete({'modelUsage': {'providerRequests': 1, 'promptTokens': 100,
                                 'completionTokens': 50, 'totalTokens': 150,
                                 'tokensComplete': True}})
    with SessionLocal() as session:
        payload = run_payload(session.get(Run, rec.id), events_for(rec.id))
    assert payload['usageReport']['totalTokens'] == 150
    assert payload['usageReport']['cost'] is None
    assert payload['usageReport']['reason'] == 'price_not_configured'


def test_same_generation_patch_cannot_reopen_a_sealed_run(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    stale = RunRecorder.restore(rec.id)
    assert rec.complete({'validation': {'trip': {'id': 'original'}}})
    assert stale.patch({'validation': {'trip': {'id': 'wrong'}}}) is False
    assert stale.patch({'status': 'running'}) is False
    assert stale.patch({'modelUsage': {'totalTokens': 12}}) is True
    with SessionLocal() as session:
        result = json.loads(session.get(Run, rec.id).result)
    assert result['status'] == 'completed'
    assert result['validation']['trip']['id'] == 'original'
    assert result['modelUsage']['totalTokens'] == 12
    assert [event.phase for event in events_for(rec.id)] == ['completed']


def test_two_real_concurrent_finishers_produce_one_terminal(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    from threading import Barrier
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    barrier = Barrier(2)
    def complete(value):
        writer = RunRecorder.restore(rec.id)
        barrier.wait(timeout=5)
        return writer.complete({'winner': value})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(complete, (1, 2)))
    assert sorted(outcomes) == [False, True]
    assert [event.phase for event in events_for(rec.id)] == ['completed']
    with SessionLocal() as session:
        assert json.loads(session.get(Run, rec.id).result)['status'] == 'completed'


def test_two_concurrent_restarts_open_exactly_one_activation(client):
    """A double reply must not start two workers or reuse an event seq."""
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    from threading import Barrier
    rec = RunRecorder('owner', 'trip', 0, 'conversation')
    assert rec.finish('needs_input', {'question': '哪天出发？'})
    barrier = Barrier(2)

    def resume(_):
        writer = RunRecorder.restore(rec.id)
        barrier.wait(timeout=5)
        return writer.restart()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(resume, (1, 2)))
    assert sorted(outcomes) == [False, True], outcomes
    with SessionLocal() as session:
        row = session.get(Run, rec.id)
        assert row.generation == 1
        assert json.loads(row.result)['status'] == 'running'
    phases = [e.phase for e in events_for(rec.id)]
    assert phases.count('resumed') == 1, phases
    seqs = [e.seq for e in events_for(rec.id)]
    assert len(seqs) == len(set(seqs)) == 2, seqs


def test_restart_versus_finish_race_keeps_one_valid_writer(client):
    """Whichever wins, the loser is fenced out of result and event writes."""
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    from threading import Barrier
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    barrier = Barrier(2)

    def finish(_):
        barrier.wait(timeout=5)
        return rec.complete({'plan': 'final'})

    def resume(_):
        barrier.wait(timeout=5)
        return RunRecorder.restore(rec.id).restart()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(finish, 0)
        b = pool.submit(resume, 0)
        finished, resumed = a.result(), b.result()
    assert (finished, resumed) in ((True, False), (False, True))
    with SessionLocal() as session:
        row = session.get(Run, rec.id)
        result = json.loads(row.result)
        generation = row.generation
    events = events_for(rec.id)
    assert len({e.seq for e in events}) == len(events), [e.seq for e in events]
    if finished:
        assert generation == 0 and result['status'] == 'completed' and result['plan'] == 'final'
        assert [e.phase for e in events] == ['completed']
    else:
        assert generation == 1 and result['status'] == 'running'
        assert 'plan' not in result
        assert 'resumed' in [e.phase for e in events]


def test_concurrent_finishers_keep_the_winner_payload_and_unique_seq(client):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    from threading import Barrier
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    barrier = Barrier(2)

    def complete(value):
        writer = RunRecorder.restore(rec.id)
        barrier.wait(timeout=5)
        return value, writer.complete({'winner': value, 'validation': {'trip': {'id': str(value)}}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(complete, (1, 2)))
    winner = next(value for value, ok in outcomes if ok)
    assert sorted(ok for _, ok in outcomes) == [False, True]
    with SessionLocal() as session:
        result = json.loads(session.get(Run, rec.id).result)
    assert result['status'] == 'completed' and result['winner'] == winner
    assert result['validation']['trip']['id'] == str(winner)
    events = events_for(rec.id)
    assert [e.phase for e in events] == ['completed']
    assert len({e.seq for e in events}) == len(events)


def test_finish_rolls_back_event_and_result_together(client, monkeypatch):
    from backend.db import Run, SessionLocal
    from backend.runlog import RunRecorder, events_for
    from sqlalchemy import event
    rec = RunRecorder('owner', 'trip', 0, 'agent')
    def reject_update(*args):
        raise RuntimeError('database write failed')
    event.listen(Run, 'before_update', reject_update)
    try:
        with pytest.raises(RuntimeError, match='database write failed'):
            rec.complete({'plan': 'not committed'})
    finally:
        event.remove(Run, 'before_update', reject_update)
    assert events_for(rec.id) == []
    with SessionLocal() as session:
        assert json.loads(session.get(Run, rec.id).result)['status'] == 'running'

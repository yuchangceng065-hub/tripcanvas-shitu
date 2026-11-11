"""Persisted planning-run stages. Counts and phases only, never prompts or keys.

Concurrency contract
--------------------
`runs.generation` is a monotonic activation token. Every `restart()` bumps it,
so a background task left over from an earlier activation is fenced out of both
event writes and result writes. `event()` allocates `seq` inside the same
transaction that holds a row lock on the parent run, so two restored recorders
can never reuse a sequence number (which would silently drop SSE events).

A run that reached a non-resumable terminal (`completed`, `gave_up`,
`cancelled`, `incomplete`) is sealed: `restart()` refuses to reopen it and no
further Agent events are accepted. The only post-seal event allowed is the
authoritative `confirmed` marker written by `/planner/confirm`.
"""
from __future__ import annotations
import json
import logging
import math
import os
import time
import uuid
from contextlib import nullcontext

from sqlalchemy import select, update, func

from backend.db import Run, RunEvent, SessionLocal
from backend.limits import run_ttl_seconds
from backend.model_governance import price_table, usage_report, usage_snapshot

LOG = logging.getLogger('tripcanvas.runlog')
SEALED_STATUSES = {'completed', 'gave_up', 'cancelled', 'incomplete'}
# Pauses are not run endings: a resumed run must not freeze a billing snapshot.
SNAPSHOT_STATUSES = SEALED_STATUSES | {'failed', 'interrupted'}
CONFIRMED_PHASE = 'confirmed'


def billing_snapshot(run: Run, result: dict) -> dict:
    """Freeze model, usage completeness, price version and cost at run end.

    The report is computed once from the price table in effect at terminal
    time. Unknown usage stays unknown, and a later price change can never
    rewrite a stored report.
    """
    return usage_snapshot(result.get('modelUsage'), run_id=run.id, owner_id=run.owner,
                          model=os.getenv('MODEL_NAME') or None, price=price_table())


def encode_detail(value: dict, limit: int = 4000) -> str:
    """Bound fields before JSON encoding, so every stored summary stays readable."""
    for width, length, depth in ((24, 1600, 7), (10, 240, 5), (5, 100, 4), (3, 60, 3)):
        truncated = False
        def trim(item, level=0):
            nonlocal truncated
            if isinstance(item, str):
                if len(item) > length:truncated = True
                return item[:length]
            if item is None or isinstance(item, (bool, int)):return item
            if isinstance(item, float):return item if math.isfinite(item) else None
            if level >= depth:
                truncated = True;return '…'
            if isinstance(item, dict):
                if len(item) > width:truncated = True
                return {str(k)[:80]: trim(v, level + 1) for k, v in list(item.items())[:width]}
            if isinstance(item, (list, tuple)):
                if len(item) > width:truncated = True
                return [trim(v, level + 1) for v in item[:width]]
            truncated = True;return str(item)[:length]
        bounded = trim(value)
        if not isinstance(bounded, dict):bounded = {'summary': bounded}
        if truncated:bounded['truncated'] = True
        encoded = json.dumps(bounded, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(encoded) <= limit:return encoded
    return json.dumps({'truncated': True, 'message': '事件摘要较长，部分字段已省略'}, ensure_ascii=False)


def decode_detail(raw: str) -> dict:
    """Older releases may have saved a sliced JSON document."""
    try:
        value = json.loads(raw)
        if isinstance(value, dict):return value
    except (ValueError, TypeError):pass
    return {'truncated': True, 'message': '历史事件摘要不完整，已跳过'}


def _run_status(run: Run) -> str | None:
    try:
        return json.loads(run.result).get('status')
    except (ValueError, TypeError, AttributeError):
        return None


def _locked_run(session, run_id: str) -> Run | None:
    """Row-level lock serialises seq allocation across processes on PostgreSQL."""
    # A no-op write also serialises SQLite, which ignores FOR UPDATE.
    session.execute(update(Run).where(Run.id == run_id).values(generation=Run.generation))
    return session.execute(
        select(Run).where(Run.id == run_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _next_seq(session, run_id: str) -> int:
    return (session.scalar(
        select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0) + 1


class RunRecorder:
    def __init__(self, owner: str, trip_id: str, base_revision: int, kind: str):
        self.id = str(uuid.uuid4())
        self.seq = 0
        self.generation = 0
        self.created = time.time()
        self.result = {'kind': kind, 'status': 'running'}
        with SessionLocal.begin() as session:
            session.add(Run(id=self.id, owner=owner, trip_id=trip_id, base_revision=base_revision,
                            result=json.dumps(self.result, ensure_ascii=False),
                            created=self.created, generation=self.generation))

    @classmethod
    def restore(cls, run_id, *, session=None):
        recorder = cls.__new__(cls)
        with (SessionLocal() if session is None else nullcontext(session)) as session:
            run = session.get(Run, run_id)
            if run is None:raise ValueError('运行不存在')
            recorder.id = run_id
            recorder.seq = session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0
            recorder.result = json.loads(run.result)
            recorder.generation = run.generation
            recorder.created = run.created
        return recorder

    def restart(self) -> bool:
        """Start a new activation. Refuses a sealed run and fences stale writers."""
        with SessionLocal.begin() as session:
            run = _locked_run(session, self.id)
            if run is None:return False
            if _run_status(run) in SEALED_STATUSES:
                LOG.warning('run %s is sealed; refusing restart', self.id)
                return False
            if time.time() - run.created > run_ttl_seconds():
                LOG.warning('run %s restart refused: exceeded TTL of %ss', self.id, run_ttl_seconds())
                return False
            if run.generation != self.generation:
                LOG.warning('run %s restart from stale generation %s', self.id, self.generation)
                return False
            result = {'kind': self.result.get('kind', 'agent'), 'status': 'running'}
            # A failed/interrupted run can be retried. Keep its earlier billing
            # evidence instead of silently discarding it on the new activation.
            try:
                prior = json.loads(run.result)
                if not isinstance(prior, dict):
                    prior = {}
            except (TypeError, ValueError):
                prior = {}
            if prior.get('usageSnapshot'):
                result['usageSnapshotHistory'] = [
                    *prior.get('usageSnapshotHistory', []), prior['usageSnapshot']]
            generation = run.generation + 1
            updated = session.execute(
                update(Run).where(Run.id == self.id, Run.generation == run.generation)
                .values(generation=generation, result=json.dumps(result, ensure_ascii=False)))
            if updated.rowcount != 1:return False
        self.result = result
        self.generation = generation
        self.event('resumed')
        return True

    def event(self, phase: str, status: str = 'ok', detail: dict | None = None) -> int | None:
        """Atomically allocate seq under a parent-row lock; drop stale writes."""
        if phase == CONFIRMED_PHASE:return None
        with SessionLocal.begin() as session:
            run = _locked_run(session, self.id)
            if run is None or run.generation != self.generation:
                LOG.warning('run %s event %s dropped: stale generation %s',
                            self.id, phase, self.generation)
                return None
            if _run_status(run) in SEALED_STATUSES:
                LOG.warning('run %s event %s dropped: run already sealed', self.id, phase)
                return None
            seq = _next_seq(session, self.id)
            session.add(RunEvent(id=str(uuid.uuid4()), run_id=self.id, seq=seq, phase=phase,
                                 status=status, detail=encode_detail(detail or {}),
                                 created=time.time()))
        self.seq = seq
        return seq

    def finish(self, status: str, payload: dict | None = None) -> bool:
        with SessionLocal.begin() as session:
            run = _locked_run(session, self.id)
            if run is None or run.generation != self.generation or _run_status(run) != 'running':
                return False
            seq = _next_seq(session, self.id)
            result = {**json.loads(run.result), **(payload or {}), 'status': status}
            if (status in SNAPSHOT_STATUSES and result.get('modelUsage')
                    and 'usageSnapshot' not in result):
                result['usageSnapshot'] = billing_snapshot(run, result)
            session.add(RunEvent(id=str(uuid.uuid4()), run_id=self.id, seq=seq, phase=status,
                                 status='ok', detail=encode_detail({'steps': seq}), created=time.time()))
            run.result = json.dumps(result, ensure_ascii=False)
        self.seq = seq
        self.result = result
        return True

    def complete(self, payload: dict | None = None) -> bool:
        return self.finish('completed', payload)

    def fail(self, message: object) -> bool:
        return self.finish('failed', {'error': str(message)[:300]})

    def patch(self, extra: dict) -> bool:
        with SessionLocal.begin() as session:
            run = _locked_run(session, self.id)
            if run is None or run.generation != self.generation or 'status' in extra:
                return False
            # Final usage is collected by the worker's finally block. It may
            # enrich metadata, but cannot replace a completed plan or status.
            if _run_status(run) in SEALED_STATUSES and not set(extra) <= {'modelUsage', 'engine'}:
                return False
            result = {**json.loads(run.result), **extra}
            # The worker's finally block delivers final usage AFTER the terminal
            # write. Freeze the report exactly once, from that final usage.
            if (result.get('status') in SNAPSHOT_STATUSES and 'modelUsage' in extra
                    and 'usageSnapshot' not in result):
                result['usageSnapshot'] = billing_snapshot(run, result)
            run.result = json.dumps(result, ensure_ascii=False)
        self.result = result
        return True

    def confirm(self, detail: dict, session=None) -> bool:
        """Authoritative post-terminal write marker from the confirm endpoint.

        This is deliberately NOT an Agent event: the Agent has no way to emit
        `confirmed`, and the confirm handler is the only caller. Exactly one
        marker per completed run; retries are idempotent.
        """
        with (SessionLocal.begin() if session is None else nullcontext(session)) as session:
            run = _locked_run(session, self.id)
            # Same fencing contract as event()/finish()/patch(): a recorder
            # restored from an older activation must not write the marker.
            if (run is None or run.generation != self.generation
                    or _run_status(run) != 'completed'):return False
            existing = session.scalar(
                select(func.count(RunEvent.id)).where(
                    RunEvent.run_id == self.id, RunEvent.phase == CONFIRMED_PHASE))
            if existing:return True
            seq = _next_seq(session, self.id)
            session.add(RunEvent(id=str(uuid.uuid4()), run_id=self.id, seq=seq,
                                 phase=CONFIRMED_PHASE, status='ok',
                                 detail=encode_detail(detail), created=time.time()))
            session.execute(update(Run).where(Run.id == self.id).values(applied=1))
        self.seq = seq
        return True

def events_for(run_id: str, cursor: int = 0) -> list[RunEvent]:
    with SessionLocal() as session:
        return list(session.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > cursor)
                                    .order_by(RunEvent.seq)))


def run_payload(run: Run, events: list[RunEvent]) -> dict:
    payload = json.loads(run.result)
    payload['runId'] = run.id
    payload['events'] = [{'seq': event.seq, 'phase': event.phase, 'status': event.status,
                          'createdAt':event.created,'detail': decode_detail(event.detail)} for event in events]
    # A stored terminal snapshot is historical evidence: prefer it over a live
    # recomputation so a later price-table change can never rewrite past cost.
    stored = payload.get('usageSnapshot')
    if isinstance(stored, dict) and isinstance(stored.get('report'), dict):
        payload['usageReport'] = stored['report']
        payload['usageReportSource'] = 'terminal-snapshot'
        return payload
    # Running / paused runs have no frozen report yet. Unconfigured prices or
    # a missing prompt/completion split keep cost=None rather than inventing 0.
    usage = payload.get('modelUsage')
    if isinstance(usage, dict):
        normalized = {
            'providerRequests': usage.get('providerRequests', 0),
            'repairRequests': usage.get('repairRequests', 0),
            'providerSeconds': usage.get('providerSeconds', 0),
            'totalTokens': usage.get('totalTokens', 0),
            'tokensComplete': usage.get('tokensComplete', False),
        }
        # Only claim a breakdown exists when the provider actually reported one.
        for key in ('promptTokens', 'completionTokens'):
            if key in usage:
                normalized[key] = usage[key]
        payload['usageReport'] = usage_report(normalized)
        payload['usageReportSource'] = 'live-view'
    return payload

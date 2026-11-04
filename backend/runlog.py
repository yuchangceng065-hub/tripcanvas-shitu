"""Persisted planning-run stages. Counts and phases only, never prompts or keys."""
from __future__ import annotations
import json
import time
import uuid
import math

from sqlalchemy import select, update, func

from backend.db import Run, RunEvent, SessionLocal


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


class RunRecorder:
    def __init__(self, owner: str, trip_id: str, base_revision: int, kind: str):
        self.id = str(uuid.uuid4())
        self.seq = 0
        self.result = {'kind': kind, 'status': 'running'}
        with SessionLocal.begin() as session:
            session.add(Run(id=self.id, owner=owner, trip_id=trip_id, base_revision=base_revision,
                            result=json.dumps(self.result, ensure_ascii=False), created=time.time()))

    @classmethod
    def restore(cls, run_id):
        recorder = cls.__new__(cls)
        with SessionLocal() as session:
            run = session.get(Run, run_id)
            if run is None:raise ValueError('运行不存在')
            recorder.id = run_id
            recorder.seq = session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0
            recorder.result = json.loads(run.result)
        return recorder

    def restart(self):
        self.result = {'kind': self.result.get('kind', 'agent'), 'status': 'running'}
        self._store(self.result)
        self.event('resumed')

    def event(self, phase: str, status: str = 'ok', detail: dict | None = None) -> int:
        self.seq += 1
        with SessionLocal.begin() as session:
            session.add(RunEvent(id=str(uuid.uuid4()), run_id=self.id, seq=self.seq, phase=phase, status=status,
                                 detail=encode_detail(detail or {}), created=time.time()))
        return self.seq

    def finish(self, status: str, payload: dict | None = None) -> None:
        self.event(status, detail={'steps': self.seq + 1})
        self.result = {**(payload or {}), 'status': status}
        self._store(self.result)

    def complete(self, payload: dict | None = None) -> None:
        self.finish('completed', payload)

    def fail(self, message: object) -> None:
        self.finish('failed', {'error': str(message)[:300]})

    def patch(self, extra: dict) -> None:
        self.result = {**self.result, **extra}
        self._store(self.result)

    def _store(self, value: dict) -> None:
        with SessionLocal.begin() as session:
            session.execute(update(Run).where(Run.id == self.id).values(result=json.dumps(value, ensure_ascii=False)))


def events_for(run_id: str, cursor: int = 0) -> list[RunEvent]:
    with SessionLocal() as session:
        return list(session.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > cursor)
                                    .order_by(RunEvent.seq)))


def run_payload(run: Run, events: list[RunEvent]) -> dict:
    payload = json.loads(run.result)
    payload['runId'] = run.id
    payload['events'] = [{'seq': event.seq, 'phase': event.phase, 'status': event.status,
                          'detail': decode_detail(event.detail)} for event in events]
    return payload

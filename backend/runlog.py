"""Persisted planning-run stages. Counts and phases only, never prompts or keys."""
from __future__ import annotations
import json
import time
import uuid

from sqlalchemy import select, update

from backend.db import Run, RunEvent, SessionLocal


class RunRecorder:
    def __init__(self, owner: str, trip_id: str, base_revision: int, kind: str):
        self.id = str(uuid.uuid4())
        self.seq = 0
        with SessionLocal.begin() as session:
            session.add(Run(id=self.id, owner=owner, trip_id=trip_id, base_revision=base_revision,
                            result=json.dumps({'kind': kind, 'status': 'running'}, ensure_ascii=False),
                            created=time.time()))

    def event(self, phase: str, status: str = 'ok', detail: dict | None = None) -> int:
        self.seq += 1
        with SessionLocal.begin() as session:
            session.add(RunEvent(id=str(uuid.uuid4()), run_id=self.id, seq=self.seq, phase=phase, status=status,
                                 detail=json.dumps(detail or {}, ensure_ascii=False)[:4000], created=time.time()))
        return self.seq

    def complete(self, payload: dict | None = None) -> None:
        self.event('completed', detail={'steps': self.seq + 1})
        self._store({**(payload or {}), 'status': 'completed', 'steps': self.seq})

    def fail(self, message: object) -> None:
        text = str(message)[:300]
        self.event('failed', status='error', detail={'message': text})
        self._store({'status': 'failed', 'error': text, 'steps': self.seq})

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
                          'detail': json.loads(event.detail)} for event in events]
    return payload

"""Small, explicit cross-task preference memory (no chat transcripts)."""
from __future__ import annotations
import json
import time

from fastapi import HTTPException
from sqlalchemy import delete

from backend.db import SessionLocal, UserPreference

SOFT_KEYS = ('pace', 'interests', 'walkBudgetKm')


def load_preferences(owner: str) -> dict:
    with SessionLocal() as session:
        row = session.get(UserPreference, owner)
        return json.loads(row.data) if row else {}


def _validate(value: dict) -> dict:
    clean: dict = {}
    if 'pace' in value:
        if value['pace'] not in ('relaxed', 'balanced', 'full'):
            raise HTTPException(422, '节奏取值无效')
        clean['pace'] = value['pace']
    if 'interests' in value:
        interests = value['interests']
        if not isinstance(interests, list) or len(interests) > 8 or any(not isinstance(item, str) or len(item) > 40 for item in interests):
            raise HTTPException(422, '兴趣需要是 8 项以内的短文本')
        clean['interests'] = list(dict.fromkeys(interests))
    if 'walkBudgetKm' in value:
        budget = value['walkBudgetKm']
        if budget is None:
            clean['walkBudgetKm'] = None
        elif not isinstance(budget, (int, float)) or isinstance(budget, bool) or not 0.5 <= budget <= 30:
            raise HTTPException(422, '步行预算需要在 0.5—30 公里之间')
        else:
            clean['walkBudgetKm'] = float(budget)
    return clean


def save_preferences(owner: str, patch: dict) -> dict:
    clean = _validate(patch)
    with SessionLocal.begin() as session:
        row = session.get(UserPreference, owner)
        current = json.loads(row.data) if row else {}
        current.update(clean)
        if row:
            row.data = json.dumps(current, ensure_ascii=False)
            row.updated = time.time()
        else:
            session.add(UserPreference(owner=owner, data=json.dumps(current, ensure_ascii=False), updated=time.time()))
    return current


def forget_preferences(owner: str) -> None:
    with SessionLocal.begin() as session:
        session.execute(delete(UserPreference).where(UserPreference.owner == owner))


def remember_from_requirements(owner: str, requirements) -> dict:
    """Learn only soft preferences after a confirmed save; hard constraints stay per task."""
    if requirements is None:
        return load_preferences(owner)
    patch = {}
    if getattr(requirements, 'pace', None):
        patch['pace'] = requirements.pace
    if getattr(requirements, 'interests', None):
        patch['interests'] = requirements.interests
    if getattr(requirements, 'walkBudgetKm', None):
        patch['walkBudgetKm'] = requirements.walkBudgetKm
    return save_preferences(owner, patch) if patch else load_preferences(owner)


def apply_preferences(facts: dict, preferences: dict) -> dict:
    """Fill only missing soft fields; explicit user facts always win."""
    if not preferences:
        return facts
    for key in ('pace', 'walkBudgetKm'):
        if facts.get(key) is None and preferences.get(key) is not None:
            facts[key] = preferences[key]
    if not facts.get('interests') and preferences.get('interests'):
        facts['interests'] = list(preferences['interests'])
    return facts

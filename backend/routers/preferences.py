from fastapi import APIRouter, Depends

from backend.app import current_user
from backend.db import User
from backend.preferences import forget_preferences, load_preferences, save_preferences
from backend.schemas import StrictModel

router = APIRouter()


class PreferencePatch(StrictModel):
    pace: str | None = None
    interests: list[str] | None = None
    walkBudgetKm: float | None = None


@router.get('/api/v1/preferences')
def get_preferences(user: User = Depends(current_user)):
    return load_preferences(user.id)


@router.put('/api/v1/preferences')
def put_preferences(data: PreferencePatch, user: User = Depends(current_user)):
    return save_preferences(user.id, data.model_dump(exclude_none=True))


@router.delete('/api/v1/preferences')
def delete_preferences(user: User = Depends(current_user)):
    forget_preferences(user.id)
    return {'ok': True}

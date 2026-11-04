import os
from fastapi import APIRouter,Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app import COMMIT,amap_enabled,db,model_api_key
from backend.db import DATABASE_URL
router=APIRouter()

@router.get('/api/v1/health')
def health(s:Session=Depends(db)):
    s.execute(select(1))
    enabled=os.getenv('PLANNER_MODE','auto')!='rules' and bool(model_api_key()) and all(os.getenv(k) for k in ('MODEL_BASE_URL','MODEL_NAME'))
    return {'status':'ok','model':enabled,'plannerMode':'model' if enabled else 'rules','map':'amap' if amap_enabled() else 'osm','routing':'amap' if amap_enabled() else 'osrm','storage':'postgresql' if DATABASE_URL.startswith('postgres') else 'sqlite','commit':COMMIT[:12] or None}


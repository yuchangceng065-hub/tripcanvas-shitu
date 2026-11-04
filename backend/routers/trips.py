import hashlib,json,secrets,time,uuid
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from sqlalchemy import delete,select
from sqlalchemy.orm import Session
from backend.app import amap_enabled,current_user,db,owned,persist_trip,route_body,throttle
from backend.db import Run,Share,TripRow,VersionRow,User
from backend.planning import evaluate
from backend.places import POIS
from backend.schemas import Trip,TripBody
router=APIRouter()

@router.get('/api/v1/trips')
def list_trips(user:User=Depends(current_user),s:Session=Depends(db)):
    return [json.loads(t.snapshot) for t in s.scalars(select(TripRow).where(TripRow.owner==user.id))]

@router.get('/api/v1/trips/{id}')
def get_trip(id:str,user:User=Depends(current_user),s:Session=Depends(db)):return json.loads(owned(s,id,user).snapshot)


@router.put('/api/v1/trips/{id}')
def save_trip(id:str,data:TripBody,user:User=Depends(current_user),s:Session=Depends(db)):
    if id!=data.trip.id:raise HTTPException(422,'旅行 ID 不一致')
    return {'id':id,'revision':persist_trip(s,user,data.trip)}

@router.delete('/api/v1/trips/{id}')
def delete_trip(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    t=owned(s,id,user)
    for model in (Share,VersionRow,Run):s.execute(delete(model).where(model.trip_id==id))
    s.delete(t);s.commit();return {'ok':True}

@router.get('/api/v1/trips/{id}/versions')
def versions(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    owned(s,id,user);return [{'id':v.id,'revision':v.revision,'trip':json.loads(v.snapshot)} for v in s.scalars(select(VersionRow).where(VersionRow.trip_id==id).order_by(VersionRow.created.desc()))]

@router.post('/api/v1/trips/{id}/validate')
async def check_plan(id:str,data:TripBody,request:Request,user:User=Depends(current_user)):
    if id!=data.trip.id:raise HTTPException(422,'旅行 ID 不一致')
    throttle('validate:'+user.id,15)
    routes={}
    for i,day in enumerate(data.trip.days):
        if len(day.activities)>1:
            routes[i]=await request.app.state.router.route(route_body(data.trip,day))
    return {'issues':evaluate(data.trip,routes,POIS),'route_source':'amap' if amap_enabled() else 'osrm','opening_hours':'unknown'}


@router.post('/api/v1/trips/{id}/shares')
def share(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    t=owned(s,id,user);token=secrets.token_urlsafe(24);share_id=str(uuid.uuid4())
    public=Trip.model_validate_json(t.snapshot).model_dump(mode='json',exclude_none=True)
    # Only schema-approved public POIs and fields are present. No user/account data.
    s.add(Share(id=share_id,token=hashlib.sha256(token.encode()).hexdigest(),owner=user.id,trip_id=id,snapshot=json.dumps(public),expires=time.time()+86400*30));s.commit();return {'id':share_id,'token':token,'expires_in_days':30}

@router.delete('/api/v1/shares/{id}')
def revoke(id:str,user:User=Depends(current_user),s:Session=Depends(db)):
    row=s.get(Share,id)
    if not row or row.owner!=user.id:raise HTTPException(404,'链接不存在')
    s.delete(row);s.commit();return {'ok':True}

@router.get('/api/v1/public/shares/{token}')
def public_share(token:str,response:Response,s:Session=Depends(db)):
    response.headers['Cache-Control']='no-store'
    row=s.scalar(select(Share).where(Share.token==hashlib.sha256(token.encode()).hexdigest()))
    if not row or row.expires<time.time():raise HTTPException(404,'分享链接不存在或已撤销')
    return json.loads(row.snapshot)


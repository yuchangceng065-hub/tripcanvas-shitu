import hashlib,hmac,uuid
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from sqlalchemy import delete,select
from sqlalchemy.orm import Session
from backend.app import client_ip,current_user,db,login_cookie,password_hash,session_token,throttle
from backend.db import AuthSession,User
from backend.schemas import Credentials
router=APIRouter()

@router.post('/api/v1/auth/register')
def register(data:Credentials,response:Response,request:Request,s:Session=Depends(db)):
    throttle('auth:'+client_ip(request),8)
    if s.scalar(select(User).where(User.username==data.username)):raise HTTPException(409,'用户名已存在')
    user=User(id=str(uuid.uuid4()),username=data.username,password=password_hash(data.password));s.add(user);s.commit();return login_cookie(response,user,s)

@router.post('/api/v1/auth/login')
def login(data:Credentials,response:Response,request:Request,s:Session=Depends(db)):
    throttle('auth:'+client_ip(request),8)
    user=s.scalar(select(User).where(User.username==data.username))
    if not user or not hmac.compare_digest(password_hash(data.password,user.password.split(':')[0]),user.password):raise HTTPException(401,'用户名或密码不正确')
    return login_cookie(response,user,s)

@router.post('/api/v1/auth/logout')
def logout(request:Request,response:Response,s:Session=Depends(db)):
    token=session_token(request);s.execute(delete(AuthSession).where(AuthSession.token==hashlib.sha256(token.encode()).hexdigest()));s.commit();response.delete_cookie('tripcanvas_session');return {'ok':True}

@router.get('/api/v1/auth/me')
def me(user:User=Depends(current_user)):return {'username':user.username}

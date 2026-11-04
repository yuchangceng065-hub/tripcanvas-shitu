"""Sessions, password hashing, rate limits and provider budgets."""
from __future__ import annotations
import hashlib, os, secrets, time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.db import AuthSession, ProviderUsage, SessionLocal, User, engine


def db():
    with SessionLocal() as s:
        yield s


def session_token(request: Request):
    authorization = request.headers.get('authorization', '')
    return authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('tripcanvas_session', '')


def current_user(request: Request, s: Session = Depends(db)):
    token = session_token(request)
    record = s.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if not record or record.expires < time.time():
        raise HTTPException(401, '请先登录')
    user = s.get(User, record.owner)
    if not user:
        raise HTTPException(401, '会话已失效')
    return user


def optional_user(request: Request, s: Session = Depends(db)):
    """Anonymous browsing is allowed; signed-in requests are keyed by account."""
    token = session_token(request)
    if not token:
        return None
    record = s.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if not record or record.expires < time.time():
        return None
    return s.get(User, record.owner)


attempts: dict[str, list[float]] = {}


def throttle(key, limit=15):
    now = time.time()
    recent = [t for t in attempts.get(key, []) if t > now - 60]
    if len(recent) >= limit:
        raise HTTPException(429, '请求较多，请稍后再试')
    attempts[key] = recent + [now]
    if len(attempts) > 5000:
        for k in list(attempts):
            if attempts[k][-1] < now - 60:
                attempts.pop(k, None)


def password_hash(p, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', p.encode(), salt.encode(), 600000).hex()


def login_cookie(response, user, s):
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(token=hashlib.sha256(token.encode()).hexdigest(), owner=user.id, expires=time.time() + 86400 * 7))
    s.commit()
    response.set_cookie('tripcanvas_session', token, httponly=True, secure=os.getenv('COOKIE_SECURE') == '1',
                        samesite='lax', max_age=86400 * 7)
    response.headers['Cache-Control'] = 'no-store'
    return {'username': user.username, 'access_token': token, 'token_type': 'bearer'}


def client_ip(request):
    """Client address as resolved by uvicorn's trusted-proxy handling.

    We deliberately do not parse X-Forwarded-For here: a caller-supplied header
    must not be able to pick its own rate-limit bucket.
    """
    return request.client.host if request.client else 'unknown'


def _reserve(session, key, limit):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    maker = sqlite_insert if engine.dialect.name == 'sqlite' else pg_insert
    session.execute(maker(ProviderUsage).values(key=key, count=0).on_conflict_do_nothing(index_elements=['key']))
    return session.execute(update(ProviderUsage).where(ProviderUsage.key == key, ProviderUsage.count < limit)
                           .values(count=ProviderUsage.count + 1).returning(ProviderUsage.count)).scalar_one_or_none()


def reserve_anonymous(kind, limit):
    """Small per-day allowance for signed-out visitors, separate from provider budgets."""
    key = 'anon:' + kind + ':' + datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with SessionLocal.begin() as session:
        if _reserve(session, key, limit) is None:
            raise HTTPException(429, '今日未登录体验额度已用完，请登录后继续；未自动升级付费额度。')


def reserve_user_daily(user_id, kind, limit):
    """Per-account daily allowance for expensive, multi-step planning calls."""
    key = 'user:' + kind + ':' + user_id + ':' + datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with SessionLocal.begin() as session:
        if _reserve(session, key, limit) is None:
            raise HTTPException(429, '今天的方案生成次数已用完，请明天再试；规则草案仍可直接生成。')


def reserve_amap_usage(category):
    groups = {'lbs': (10000, 500), 'search': (1000, 100), 'map': (5000, 500)}
    default_month, default_day = groups[category]
    caps = {'lbs': 150000, 'search': 5000, 'map': 1500000}
    month = min(caps[category], max(0, int(os.getenv('AMAP_' + category.upper() + '_MONTHLY_LIMIT', str(default_month)))))
    day = max(0, int(os.getenv('AMAP_' + category.upper() + '_DAILY_LIMIT', str(default_day))))
    now = datetime.now(timezone.utc)
    buckets = [('month:' + now.strftime('%Y-%m'), month), ('day:' + now.strftime('%Y-%m-%d'), day)]
    with SessionLocal.begin() as session:
        for bucket, limit in buckets:
            if _reserve(session, 'amap:' + category + ':' + bucket, limit) is None:
                raise HTTPException(429, '地图服务已达到本站调用预算，请稍后再试；未自动开通付费调用。')

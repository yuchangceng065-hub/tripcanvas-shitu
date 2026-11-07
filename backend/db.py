"""Database engine, session factory and ORM models.

Alembic and the API both import Base from here so the schema has one source.
"""
from __future__ import annotations
import os
from pathlib import Path

from sqlalchemy import create_engine, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).parent


def load_local_env(path=ROOT.parent / '.env'):
    """Optional .env for local runs; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()

DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{ROOT / "tripcanvas.db"}')
if DATABASE_URL.startswith(('postgres://', 'postgresql://')):
    DATABASE_URL = 'postgresql+psycopg://' + DATABASE_URL.split('://', 1)[1]
if (os.getenv('RENDER') or os.getenv('RAILWAY_ENVIRONMENT_ID')) and (not os.getenv('DATABASE_URL') or DATABASE_URL.startswith('sqlite')):
    raise RuntimeError('Cloud deployment requires a PostgreSQL DATABASE_URL; refusing ephemeral SQLite')

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    **({'connect_args': {'check_same_thread': False}} if DATABASE_URL.startswith('sqlite') else {
        'pool_size': 2, 'max_overflow': 2, 'pool_recycle': 300,
        'connect_args': {'connect_timeout': 10, 'application_name': 'tripcanvas', 'prepare_threshold': None},
    }),
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    password: Mapped[str] = mapped_column(String)


class AuthSession(Base):
    __tablename__ = 'sessions'
    token: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    expires: Mapped[float] = mapped_column(Float)


class TripRow(Base):
    __tablename__ = 'trips'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(Text)


class VersionRow(Base):
    __tablename__ = 'versions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    trip_id: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float)


class Share(Base):
    __tablename__ = 'shares'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    trip_id: Mapped[str] = mapped_column(String, index=True)
    snapshot: Mapped[str] = mapped_column(Text)
    expires: Mapped[float] = mapped_column(Float)


class PlaceRecord(Base):
    __tablename__ = 'place_catalog'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[str] = mapped_column(Text)


class ProviderUsage(Base):
    __tablename__ = 'provider_usage'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class Run(Base):
    __tablename__ = 'runs'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    trip_id: Mapped[str] = mapped_column(String, index=True)
    base_revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float)
    applied: Mapped[int] = mapped_column(Integer, default=0)
    # Monotonic activation token. Every resume bumps it; a background task
    # holding an older token is fenced out of events and result writes.
    generation: Mapped[int] = mapped_column(Integer, default=0,
                                           server_default='0', nullable=False)


class RunEvent(Base):
    __tablename__ = 'run_events'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float)


class PlanningSession(Base):
    __tablename__ = 'planning_sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(Text)
    updated: Mapped[float] = mapped_column(Float)


class ScenarioBranch(Base):
    __tablename__ = 'scenario_branches'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    source_trip_id: Mapped[str] = mapped_column(String, index=True)
    source_revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String, unique=True)
    request_hash: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    snapshot: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(Float)


class UserPreference(Base):
    __tablename__ = 'user_preferences'
    owner: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[str] = mapped_column(Text)
    updated: Mapped[float] = mapped_column(Float)

"""Apply Alembic revisions before a Render instance starts serving requests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from backend.db import Base

ROOT = Path(__file__).resolve().parents[1]
PRE_GENERATION_REVISION = "d59e925296bc"


def _adopt_legacy_database(connection: Connection, config: Config) -> None:
    inspector = inspect(connection)
    existing = set(inspector.get_table_names()) & set(Base.metadata.tables)
    if not existing:
        return

    actual = {name: {column["name"] for column in inspector.get_columns(name)} for name in existing}
    expected = {name: set(table.columns.keys()) for name, table in Base.metadata.tables.items()}
    if actual == expected:
        command.stamp(config, "head")
        return

    expected["runs"].remove("generation")
    if actual != expected:
        raise RuntimeError("Unversioned TripCanvas database does not match the supported legacy schema")
    command.stamp(config, PRE_GENERATION_REVISION)


def upgrade_database(engine: Engine) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(734598312)"))
        config.attributes["connection"] = connection
        if MigrationContext.configure(connection).get_current_revision() is None:
            _adopt_legacy_database(connection, config)
        command.upgrade(config, "head")

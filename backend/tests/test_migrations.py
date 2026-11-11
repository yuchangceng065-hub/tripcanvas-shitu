import os,subprocess,sys
from pathlib import Path
from sqlalchemy import create_engine,inspect

ROOT=Path(__file__).resolve().parents[2]

def test_alembic_baseline_matches_models(tmp_path):
    database=tmp_path/'migration.db'
    environment={**os.environ,'DATABASE_URL':f'sqlite:///{database}'}
    result=subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,env=environment,
        capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    from backend.app import Base
    tables=set(inspect(create_engine(f'sqlite:///{database}')).get_table_names())-{'alembic_version'}
    assert tables==set(Base.metadata.tables)


def test_render_upgrade_adopts_legacy_data_and_is_repeatable(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text

    from backend.migrate import PRE_GENERATION_REVISION, upgrade_database

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, PRE_GENERATION_REVISION)
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(
            text(
                "INSERT INTO runs (id, owner, trip_id, base_revision, result, created, applied) "
                "VALUES ('legacy-run', 'owner', '', 0, '{}', 0, 0)"
            )
        )

    upgrade_database(engine)
    upgrade_database(engine)

    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "b7d2e41c9a30"
        assert connection.execute(text("SELECT id, generation FROM runs")).one() == ("legacy-run", 0)


def test_render_upgrade_fresh_database(tmp_path):
    from alembic.runtime.migration import MigrationContext

    from backend.db import Base
    from backend.migrate import upgrade_database

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    upgrade_database(engine)
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        assert MigrationContext.configure(connection).get_current_revision() == "b7d2e41c9a30"

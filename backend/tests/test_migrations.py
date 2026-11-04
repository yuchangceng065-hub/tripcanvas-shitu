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

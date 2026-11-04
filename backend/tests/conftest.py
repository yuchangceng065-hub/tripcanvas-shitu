"""Isolate the entire test session before any application module is imported."""
import os
import sys
import tempfile

_database=None
if os.environ.get('TEST_DATABASE_URL'):
    os.environ['DATABASE_URL']=os.environ['TEST_DATABASE_URL']
else:
    _database=tempfile.TemporaryDirectory(prefix='tripcanvas-tests-')
    os.environ['DATABASE_URL']='sqlite:///'+_database.name+'/tests.db'
for name in ('AMAP_WEB_KEY','AMAP_JS_KEY','AMAP_JS_SECURITY_CODE','MODEL_API_KEY','GROQ_API_KEY'):
    os.environ[name]=''
os.environ['PLANNER_MODE']='rules'

def pytest_sessionfinish(session,exitstatus):
    module=sys.modules.get('backend.app')
    if module:module.engine.dispose()
    if _database:_database.cleanup()

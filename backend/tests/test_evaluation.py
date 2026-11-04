import copy,json,subprocess,sys
from pathlib import Path
from backend.evaluation import audit_option
from backend.model_metrics import empty_metrics,record_usage
ROOT=Path(__file__).resolve().parents[2]

def valid_option():
 task={'ids':['quyuan'],'days':1,'expect':'generated'}
 trip={'requirements':{'mustVisitIds':['quyuan']},'mode':'walk','days':[{'date':'2026-11-14','start':540,'end':1110,'activities':[{'id':'a','placeId':'quyuan','duration':90,'lock':'keep'}]}]}
 metric={'walkingKm':0,'travelMinutes':0,'visitMinutes':90,'freeMinutes':480,'days':[{'travelMinutes':0,'visitMinutes':90,'freeMinutes':480,'finish':630}]}
 return {'trip':trip,'routes':{},'metrics':metric,'omittedIds':[]},task

def test_oracle_rejects_wrong_metrics_and_required_omissions_without_planner_validator():
 option,task=valid_option();assert audit_option(option,task)==[]
 altered=copy.deepcopy(option);altered['metrics']['freeMinutes']=999
 assert 'total_metric_mismatch' in audit_option(altered,task)
 altered=copy.deepcopy(option);altered['trip']['days'][0]['activities']=[]
 assert 'must_visit:quyuan' in audit_option(altered,task)
 optional_task={**task,'optional':['quyuan']};altered=copy.deepcopy(option)
 altered['trip']['days'][0]['activities']*=2
 assert 'duplicate_visit:quyuan' in audit_option(altered,optional_task)

def test_evaluation_cli_runs_fixed_suite_in_isolation(tmp_path):
 p=subprocess.run([sys.executable,'scripts/evaluate.py','--output-dir',str(tmp_path)],cwd=ROOT,capture_output=True,text=True)
 assert p.returncode==0,p.stdout+p.stderr
 report=json.loads((tmp_path/'report.json').read_text());assert report['summary']['total']==30 and report['summary']['failed']==0
 assert report['externalProviderRequests']==0 and report['modelCalls']==0
 assert report['summary']['feasibleTasksSucceeded']==10 and report['summary']['invalidAccepted']==0
 assert (tmp_path/'report.md').exists()

def test_evaluator_reports_a_golden_mismatch_as_failure(tmp_path):
 suite=json.loads((ROOT/'fixtures/evaluation/cases.json').read_text());suite['cases'][0]['fields']['returnBy']=999
 path=tmp_path/'mutated.json';path.write_text(json.dumps(suite));output=tmp_path/'report'
 p=subprocess.run([sys.executable,'scripts/evaluate.py','--suite',str(path),'--case','P01','--output-dir',str(output)],cwd=ROOT,capture_output=True,text=True)
 assert p.returncode==1
 report=json.loads((output/'report.json').read_text());assert report['summary']['failed']==1
 assert 'field:returnBy' in report['results'][0]['errors']

def test_model_usage_does_not_invent_missing_counts():
 m=empty_metrics();record_usage(m,{'prompt_tokens':5,'completion_tokens':8,'total_tokens':13});assert m['totalTokens']==13 and m['tokensComplete']
 record_usage(m,None);assert m['totalTokens']==13 and not m['tokensComplete']
 assert set(m)=={'providerRequests','repairRequests','providerSeconds','promptTokens','completionTokens','totalTokens','tokensComplete'}

def test_evaluator_rejects_unknown_guard_instead_of_passing(tmp_path):
 suite=json.loads((ROOT/'fixtures/evaluation/cases.json').read_text());suite['cases'][-1]['action']='misspelled'
 path=tmp_path/'invalid.json';path.write_text(json.dumps(suite))
 p=subprocess.run([sys.executable,'scripts/evaluate.py','--suite',str(path),'--output-dir',str(tmp_path/'report')],cwd=ROOT,capture_output=True,text=True)
 assert p.returncode==2 and 'unknown guard action' in p.stderr

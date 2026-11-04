#!/usr/bin/env python3
"""30 fixed engineering tasks, isolated DB and synthetic roads, zero external calls."""
import argparse,asyncio,copy,hashlib,json,os,platform,subprocess,sys,tempfile,time
from datetime import date,datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

GUARD_ACTIONS={'return-missing','fixed-date','idempotence','stale-revision','ownership','share-revoke'}

def validate_suite(suite):
 if not isinstance(suite.get('cases'),list) or not suite['cases']:raise ValueError('cases must be a nonempty list')
 date.fromisoformat(suite['referenceDate']);ids=set()
 for task in suite['cases']:
  if not isinstance(task.get('id'),str) or task['id'] in ids:raise ValueError('missing or duplicate case ID')
  ids.add(task['id'])
  if task.get('split') not in ('development','regression') or not task.get('title'):raise ValueError('invalid split or title')
  if task.get('kind')=='parse':
   if not task.get('text') or not any(task.get(k) for k in ('fields','absent','missing','mentions')):raise ValueError('parse case requires a request and assertions')
  elif task.get('kind')=='plan':
   if task.get('expect') not in ('generated','infeasible','unavailable') or not task.get('ids') or not 1<=task.get('days',0)<=3:raise ValueError('invalid planning case')
   if len(task['ids'])!=len(set(task['ids'])) or not set(task.get('optional',[]))<=set(task['ids']):raise ValueError('invalid candidate set')
  elif task.get('kind')=='guard':
   if task.get('action') not in GUARD_ACTIONS:raise ValueError('unknown guard action')
  else:raise ValueError('unknown case kind')


def fixture_roads(task,calls):
 async def run(body):
  from fastapi import HTTPException
  calls.append(tuple(body.placeIds));kind=task.get('road','normal')
  if kind=='quota':raise HTTPException(429,'评测夹具：道路服务配额已满')
  if kind=='no-route':raise HTTPException(422,'评测夹具：这些地点之间无道路')
  minutes=100 if kind=='bad-order' and body.placeIds==task['ids'] else 10
  if body.mode=='drive' and kind!='unsplit':
   pieces=[{'mode':'walk','minutes':2,'distance':.15},{'mode':'transfer','minutes':2,'distance':0},{'mode':'drive','minutes':10,'distance':3},{'mode':'transfer','minutes':3,'distance':0},{'mode':'walk','minutes':3,'distance':.25}]
   leg={'minutes':20,'distance':3.4,'segments':pieces}
   return {'source':'amap','fixture':True,'crs':'GCJ02','legs':[copy.deepcopy(leg) for _ in body.placeIds[1:]]}
  return {'source':'osrm','fixture':True,'legs':[{'minutes':minutes,'distance':.6} for _ in body.placeIds[1:]]}
 return run


def guard(client,task):
 action=task['action'];errors=[]
 t={'id':os.urandom(10).hex(),'title':'评测旅行','city':'杭州','people':2,'mode':'walk','revision':1,'days':[{'date':'2026-11-14','start':540,'end':1110,'activities':[{'id':'a','placeId':'quyuan','duration':60,'lock':'fixed','fixedStart':600}]}]}
 def confirm(t,base):return client.post('/api/v1/planner/confirm',json={'trip':t,'baseRevision':base})
 if action=='return-missing':
  t['requirements']={'city':'杭州','startDate':'2026-11-14','days':1,'people':2,'mode':'walk','destinationPlaceId':'station','returnBy':1020}
  r=confirm(t,0)
  if r.status_code!=422 or not any(i['code']=='return_missing' for i in r.json().get('detail',{}).get('issues',[])):errors.append('invalid_return_accepted')
  return errors
 r=confirm(t,0)
 if r.status_code!=200:return ['valid_confirmation_failed']
 if action=='fixed-date':
  changed=copy.deepcopy(t);changed['days'][0]['date']='2026-11-15'
  if confirm(changed,1).status_code!=422:errors.append('fixed_date_changed')
 elif action=='idempotence':
  if confirm(t,0).status_code!=200:errors.append('replay_failed')
  if len(client.get('/api/v1/trips/'+t['id']+'/versions').json())!=1:errors.append('duplicate_versions')
 elif action=='stale-revision':
  changed=copy.deepcopy(t);changed['title']='更新';changed['revision']=2
  if confirm(changed,1).status_code!=200:errors.append('valid_update_failed')
  if confirm(t,0).status_code!=409:errors.append('stale_overwrite')
 elif action=='ownership':
  # Retain the first session and try requests using a separate signed-in identity.
  client.post('/api/v1/auth/logout')
  credential={'username':'eval_other_'+os.urandom(4).hex(),'password':'isolated-evaluation-password'}
  r=client.post('/api/v1/auth/register',json=credential)
  if r.status_code!=200:return ['other_account_unavailable']
  for suffix in ('','/versions'):
   if client.get('/api/v1/trips/'+t['id']+suffix).status_code!=404:errors.append('private_resource_exposed')
  client.post('/api/v1/auth/logout');client.post('/api/v1/auth/login',json={'username':'eval_main','password':'isolated-evaluation-password'})
 elif action=='share-revoke':
  r=client.post('/api/v1/trips/'+t['id']+'/shares',json={});shared=r.json();changed=copy.deepcopy(t);changed['title']='更新';changed['revision']=2;confirm(changed,1)
  public=client.get('/api/v1/public/shares/'+shared['token'])
  if public.status_code!=200 or public.json()['title']!=t['title']:errors.append('share_not_snapshot')
  if client.delete('/api/v1/shares/'+shared['id']).status_code!=200:errors.append('revoke_failed')
  if client.get('/api/v1/public/shares/'+shared['token']).status_code!=404:errors.append('revoked_share_readable')
 if client.get('/api/v1/trips/'+t['id']).status_code!=200:errors.append('original_lost')
 return errors


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--suite',type=Path,default=ROOT/'fixtures/evaluation/cases.json');parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--case',action='append',default=[]);args=parser.parse_args()
 suite=json.loads(args.suite.read_text())
 try:validate_suite(suite)
 except (ValueError,KeyError,TypeError) as error:parser.error('Invalid suite: '+str(error))
 if set(args.case)-{c['id'] for c in suite['cases']}:parser.error('Unknown case ID')
 cases=[c for c in suite['cases'] if not args.case or c['id'] in args.case]
 if not cases:parser.error('No matching cases')
 # Set before importing the app; do not let .env or a production database leak into evaluation.
 with tempfile.TemporaryDirectory(prefix='shitu-eval-') as isolated:
  os.environ['DATABASE_URL']='sqlite:///'+isolated+'/evaluation.db'
  for name in ('AMAP_WEB_KEY','AMAP_JS_KEY','AMAP_JS_SECURITY_CODE','MODEL_API_KEY','GROQ_API_KEY'):os.environ[name]=''
  os.environ['PLANNER_MODE']='rules'
  from backend.app import app,Trip,POIS,attempts,engine
  from backend.planning import AlternativesBody,heuristic
  from backend.requirements import parse_request
  from backend.alternatives import PlanSearch
  from backend.evaluation import audit_comparison
  from fastapi.testclient import TestClient
  import httpx
  def offline_only(*args,**kwargs):raise RuntimeError('Offline evaluation forbids external HTTP requests')
  async def offline_async_only(*args,**kwargs):raise RuntimeError('Offline evaluation forbids external HTTP requests')
  httpx.HTTPTransport.handle_request=offline_only
  httpx.AsyncHTTPTransport.handle_async_request=offline_async_only
  results=[]
  with TestClient(app) as client:
   registered=client.post('/api/v1/auth/register',json={'username':'eval_main','password':'isolated-evaluation-password'});assert registered.status_code==200
   for task in cases:
    attempts.clear();started=time.perf_counter();errors=[];details={};calls=[]
    try:
     if task['kind']=='parse':
      parsed=parse_request(task['text'],today=date.fromisoformat(suite['referenceDate']))
      for field,value in task.get('fields',{}).items():
       if parsed['requirements'].get(field)!=value:errors.append('field:'+field)
      for field in task.get('absent',[]):
       if field in parsed['requirements']:errors.append('unexpected:'+field)
      for field in task.get('missing',[]):
       if field not in parsed['missing']:errors.append('missing_confirmation:'+field)
      actual={(m['role'],m['text']) for m in parsed['mentions']}
      for mention in task.get('mentions',[]):
       if tuple(mention) not in actual:errors.append('mention:'+':'.join(mention))
      if len(actual)!=len(parsed['mentions']):errors.append('duplicate_mentions')
      details={'requirements':parsed['requirements'],'missing':parsed['missing'],'disposition':'confirmation_required' if parsed['missing'] else 'understood'}
     elif task['kind']=='plan':
      req={'city':'杭州','startDate':'2026-11-14','days':task['days'],'people':2,'mode':task.get('mode','walk'),'start':task.get('start',540),'end':task.get('end',1110),'originPlaceId':task.get('origin'),'destinationPlaceId':task.get('destination'),'returnBy':task.get('returnBy'),'walkBudgetKm':task.get('walkBudgetKm')}
      body=AlternativesBody(title=task['title'],requirements=req,candidateIds=task['ids'],optionalIds=task.get('optional',[]),useModel=False)
      body.requirements.mustVisitIds=[id for id in body.candidateIds if id not in body.optionalIds]
      seed=task.get('seed') or heuristic(body.candidateIds,POIS,body.requirements.days)
      result=asyncio.run(PlanSearch(body,POIS,POIS,fixture_roads(task,calls),Trip.model_validate,max_routes=task.get('maxRoutes',12)).search(seed))
      errors=audit_comparison(result,task,route_fixture=True)
      details={'disposition':'generated' if result['options'] else task['expect'],'options':len(result['options']),'dayRouteQueries':len(calls),'search':result['search'],'invalidAccepted':bool(result['options']) and bool(errors),'optionMetrics':[o['metrics'] for o in result['options']]}
     else:
      app.state.router.route=fixture_roads(task,calls)
      errors=guard(client,task);unsafe={'invalid_return_accepted','fixed_date_changed','stale_overwrite','private_resource_exposed','revoked_share_readable'}
      details={'disposition':'guarded','invalidAccepted':bool(unsafe.intersection(errors))}
    except Exception as error:errors=['exception:'+type(error).__name__];details={'disposition':'evaluation_error'}
    result={'id':task['id'],'title':task['title'],'kind':task['kind'],'split':task['split'],'passed':not errors,'errors':errors,'elapsedMs':round((time.perf_counter()-started)*1000,2),**details};results.append(result)
    print(f"{task['id']} {'PASS' if not errors else 'FAIL'} {','.join(errors)}",flush=True)
  engine.dispose()
 durations=sorted(r['elapsedMs'] for r in results);planning=[r for r in results if r['kind']=='plan'];positive_ids={c['id'] for c in cases if c['kind']=='plan' and c['expect']=='generated'};positive=[r for r in results if r['id'] in positive_ids]
 try:commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 except subprocess.SubprocessError:commit='unknown'
 source_paths=[*(ROOT/'backend').glob('*.py'),*(ROOT/'scripts').glob('evaluate*.py')]
 sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_paths)}
 report={'mode':'offline-contract-fixtures','externalProviderRequests':0,'modelCalls':0,'suiteVersion':suite['version'],'suiteSha256':hashlib.sha256(args.suite.read_bytes()).hexdigest(),'sourceCommit':commit,'sourceHashes':sources,'python':platform.python_version(),'generatedAt':datetime.now(timezone.utc).isoformat(),'summary':{'total':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'feasibleTasks':len(positive),'feasibleTasksSucceeded':sum(r['passed'] and r.get('options',0)>0 for r in positive),'negativePlanningTasks':len(planning)-len(positive),'invalidAccepted':sum(bool(r.get('invalidAccepted')) for r in results),'p50Ms':durations[len(durations)//2],'p95Ms':durations[min(len(durations)-1,int(len(durations)*.95))]},'results':results}
 args.output_dir.mkdir(parents=True,exist_ok=True);(args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 lines=['# 固定任务工程评测','',f"模式：离线合成道路；外部请求 0；模型调用 0。通过 {report['summary']['passed']}/{len(results)}。",'',f"可行规划任务成功 {report['summary']['feasibleTasksSucceeded']}/{len(positive)}；负向规划任务 {len(planning)-len(positive)}；错误接受 {report['summary']['invalidAccepted']}。",'','耗时为本地夹具耗时，不能作为真实 API 或 AI 性能。10 条开发、20 条固定回归，不是盲测；真实模型另行评测。','','| 编号 | 集合 | 任务 | 结果 | 耗时 ms | 失败项 |','|---|---|---|---|---:|---|']
 lines += [f"| {r['id']} | {r['split']} | {r['title']} | {'通过' if r['passed'] else '失败'} | {r['elapsedMs']} | {', '.join(r['errors']) or '—'} |" for r in results]
 (args.output_dir/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(report['summary'],ensure_ascii=False));return 0 if all(r['passed'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())

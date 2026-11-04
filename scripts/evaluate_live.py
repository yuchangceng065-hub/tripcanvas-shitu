#!/usr/bin/env python3
"""Explicit opt-in, six real Groq + AMap smoke tasks. Never loads provider keys."""
import argparse,copy,hashlib,json,os,re,secrets,time,uuid
from collections import Counter
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parent.parent
BASE='https://tripcanvas-api.onrender.com/api/v1'

def check_trip(trip,ids,days,destination=None,deadline=None):
 errors=[];anchors={destination} if destination else set()
 actual=[a['placeId'] for d in trip['days'] for a in d['activities'] if a['placeId'] not in anchors]
 if Counter(actual)!=Counter(ids):errors.append('candidate_conservation')
 if len(trip['days'])!=days:errors.append('day_count')
 for i,d in enumerate(trip['days']):
  if d['date']!=(date(2026,11,14)+timedelta(days=i)).isoformat():errors.append('date_changed')
 if destination:
  last=trip['days'][-1]['activities'][-1]
  if last['placeId']!=destination or last['lock']!='fixed' or last.get('fixedStart')!=deadline:errors.append('return_constraint')
 return errors

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--run-production-free-tier',action='store_true',required=True)
 p.add_argument('--release-commit',required=True);p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
 if not re.fullmatch('[a-f0-9]{40}',args.release_commit):p.error('release-commit must be the verified deployed full SHA')
 suite_path=ROOT/'fixtures/evaluation/live-cases.json';suite=json.loads(suite_path.read_text());results=[];seed=None;last_call=0
 report={'mode':'live-provider-smoke','releaseCommit':args.release_commit,'suiteVersion':suite['version'],'suiteSha256':hashlib.sha256(suite_path.read_bytes()).hexdigest(),'generatedAt':datetime.now(timezone.utc).isoformat(),'provider':'Groq','model':'openai/gpt-oss-20b','taskLimit':6,'providerRequestUpperBound':12,'results':results}
 args.output_dir.mkdir(parents=True,exist_ok=True)
 def save():
  report['summary']={'total':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'providerRequestsReported':sum(r.get('modelUsage',{}).get('providerRequests',0) for r in results),'totalTokensReported':sum(r.get('modelUsage',{}).get('totalTokens',0) for r in results),'usageComplete':all(r.get('modelUsage',{}).get('tokensComplete',False) for r in results)}
  (args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
  lines=['# 真实 Groq＋高德任务验收','','6条固定冒烟任务，不是通用模型准确率或性能 SLA。未自动切换模型或付费服务。','',f"已运行 {len(results)}；通过 {report['summary']['passed']}；失败 {report['summary']['failed']}。未报告的 token 用量视为未知。",'','| 编号 | 任务 | 结果 | 秒 | 模型请求 | token（报告值） | 失败项 |','|---|---|---|---:|---:|---:|---|']
  lines += [f"| {r['id']} | {r['title']} | {'通过' if r['passed'] else '失败'} | {r['seconds']} | {r.get('modelUsage',{}).get('providerRequests','未知')} | {r.get('modelUsage',{}).get('totalTokens','未知')} | {', '.join(r['errors']) or '—'} |" for r in results]
  (args.output_dir/'report.md').write_text('\n'.join(lines)+'\n')
 with httpx.Client(proxy=os.getenv('HTTPS_PROXY') or None,timeout=180,follow_redirects=False) as c:
  health=c.get(BASE+'/health');health.raise_for_status();health=health.json()
  if not health.get('model') or health.get('plannerMode')!='model' or health.get('routing')!='amap':raise RuntimeError('Expected deployed model + AMap configuration')
  report['health']=health
  account={'username':'qa_eval_'+uuid.uuid4().hex[:12],'password':secrets.token_urlsafe(32)}
  r=c.post(BASE+'/auth/register',json=account);r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['access_token'];c.cookies.clear()
  try:
   for task in suite['cases'][:6]:
    # Two potential provider requests per task fit within the global four/minute ceiling.
    remaining=35-(time.monotonic()-last_call)
    if last_call and remaining>0:time.sleep(remaining)
    started=time.monotonic();errors=[];detail={};last_call=started
    try:
     if task['kind']=='draft':
      req={'city':'杭州','startDate':'2026-11-14','days':task['days'],'people':2,'mode':'walk','start':540,'end':1140}
      if task.get('destination'):req.update(destinationPlaceId=task['destination'],returnBy=task['returnBy'])
      response=c.post(BASE+'/planner/draft',json={'title':'独立评测 '+task['title'],'requirements':req,'candidateIds':task['ids'],'useModel':True})
     else:
      if seed is None:raise ValueError('seed_unavailable')
      before=copy.deepcopy(seed);acts=before['days'][0]['activities'];acts[0]['lock']='none';acts[1]['lock']='keep'
      prompts={'shorten':'只把第一站的停留时间减少15分钟。其余全部保持原样，保留所有地点、活动ID和出发时间。', 'depart':'只把当天出发时间延后15分钟，所有活动和停留时间原样保留。', 'indoor':'下雨了，只把第一站曲院风荷替换成室内的中国丝绸博物馆（placeId=silk），保留第一站活动ID和停留时长。其他活动、锁定项和出发时间原样保留。'}
      response=c.post(BASE+'/planner/suggest',json={'trip':before,'day':0,'prompt':prompts[task['action']]})
     detail['status']=response.status_code
     if response.status_code!=200:errors.append('http_'+str(response.status_code))
     else:
      data=response.json();after=data['trip'];detail['modelUsage']=data.get('modelUsage',{})
      if data.get('source')!='model':errors.append('not_model')
      if task['kind']=='draft':
       errors+=check_trip(after,task['ids'],task['days'],task.get('destination'),task.get('returnBy'))
       if any(route.get('source')!='amap' for route in data.get('routes',{}).values()):errors.append('not_amap')
       if any(i['severity']=='error' for i in data.get('issues',[])):errors.append('road_or_time_conflict')
       if task['id']=='L01' and not errors:
        seed=copy.deepcopy(after)
        # Normalize outdoor-first for independent edit tasks, not as a model-quality result.
        seed['days'][0]['activities'].sort(key=lambda a:a['placeId']!='quyuan')
        if c.get(BASE+'/trips/'+after['id']).status_code!=404:errors.append('draft_saved_without_confirmation')
        confirmed=c.post(BASE+'/planner/confirm',json={'trip':after,'baseRevision':0})
        replay=c.post(BASE+'/planner/confirm',json={'trip':after,'baseRevision':0})
        if confirmed.status_code!=200 or replay.status_code!=200:errors.append('confirm_or_replay')
        elif len(c.get(BASE+'/trips/'+after['id']+'/versions').json())!=1:errors.append('duplicate_versions')
      else:
       expected=copy.deepcopy(before);first=expected['days'][0]['activities'][0]
       if task['action']=='shorten':first['duration']-=15
       elif task['action']=='depart':expected['days'][0]['start']+=15
       else:first['placeId']='silk'
       if after['days']!=expected['days']:errors.append('edit_semantics_or_preservation')
       if after.get('requirements')!=before.get('requirements'):errors.append('requirements_changed')
       preview=c.post(BASE+'/planner/preview',json={'trip':after})
       if preview.status_code!=200:errors.append('preview_unavailable')
       elif any(i['severity']=='error' for i in preview.json().get('issues',[])):errors.append('road_or_time_conflict')
       run=c.get(BASE+'/runs/'+data['id'])
       if run.status_code!=200 or run.json()['trip']!=after:errors.append('run_not_persisted')
    except (httpx.HTTPError,KeyError,IndexError,ValueError) as error:errors.append('exception_'+type(error).__name__)
    result={'id':task['id'],'title':task['title'],'passed':not errors,'errors':errors,'seconds':round(time.monotonic()-started,2),**detail};results.append(result);save();print(json.dumps(result,ensure_ascii=False),flush=True)
  finally:c.post(BASE+'/auth/logout')
 print(json.dumps(report['summary'],ensure_ascii=False));return int(any(not r['passed'] for r in results))
if __name__=='__main__':raise SystemExit(main())

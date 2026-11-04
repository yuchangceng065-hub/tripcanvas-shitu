"""Independent acceptance oracle. Does not call the planner or its validator."""
from datetime import date,timedelta
import math

# Human-fixed lower bounds for the curated evaluation POIs, in minutes.
MIN_VISIT={'quyuan':60,'museum':60,'art':60,'leifeng':60,'silk':60,'hubin':45,'coffee':30}


def audit_option(option,task,route_fixture=False):
    errors=[];t=option['trip'];r=t['requirements'];routes=option['routes'];metrics=option['metrics']
    candidates=set(task['ids']);must=candidates-set(task.get('optional',[]))
    present=[a['placeId'] for d in t['days'] for a in d['activities']]
    for id in must:
        if present.count(id)!=1:errors.append('must_visit:'+id)
    for id in candidates:
        if present.count(id)>1:errors.append('duplicate_visit:'+id)
    if set(option['omittedIds'])!=candidates-set(present):errors.append('omissions_not_exact')
    if not set(option['omittedIds'])<=set(task.get('optional',[])):errors.append('required_omitted')
    anchors={task.get('origin'),task.get('destination')}-{None}
    if set(present)-candidates-anchors:errors.append('unknown_place')
    if len(t['days'])!=task['days']:errors.append('day_count')
    if set(r.get('mustVisitIds',[]))!=must:errors.append('required_metadata')
    travel=visits=free=walking=0;walk_known=True;day_walk=[]
    for di,day in enumerate(t['days']):
        expected_date=(date.fromisoformat('2026-11-14')+timedelta(days=di)).isoformat()
        if day['date']!=expected_date:errors.append('date')
        if day['start']!=task.get('start',540) or day['end']!=task.get('end',1110):errors.append('time_bounds_changed')
        route=routes.get(di,routes.get(str(di)))
        legs=(route or {}).get('legs',[])
        if len(legs)!=max(0,len(day['activities'])-1):errors.append('missing_road_legs');continue
        if route_fixture and route and not route.get('fixture'):errors.append('unexpected_live_route')
        time=day['start'];day_free=day_visits=day_travel=day_walking=0;known=True
        for ai,a in enumerate(day['activities']):
            if ai:
                leg=legs[ai-1];minutes=leg.get('minutes')
                if not isinstance(minutes,(int,float)) or not math.isfinite(minutes) or minutes<0:errors.append('invalid_road_time');continue
                time+=minutes;day_travel+=minutes
                if t['mode']=='walk':day_walking+=leg['distance']
                elif leg.get('segments') is not None:day_walking+=sum(s['distance'] for s in leg['segments'] if s['mode']=='walk')
                else:known=False
            earliest=time
            if a['lock']=='fixed':
                if earliest>a['fixedStart']:errors.append('late_fixed_appointment')
                day_free+=max(0,a['fixedStart']-earliest);time=max(earliest,a['fixedStart'])
            if a['placeId'] in candidates:
                day_visits+=a['duration']
                if a['duration']<MIN_VISIT.get(a['placeId'],30):errors.append('dwell_below_reviewed_minimum')
            time+=a['duration']
            if di==len(t['days'])-1 and a['placeId']==task.get('destination') and task.get('returnBy') is not None:
                if earliest>task['returnBy']:errors.append('late_return')
                if ai!=len(day['activities'])-1 or a.get('fixedStart')!=task['returnBy']:errors.append('return_not_final_or_changed')
        if time>day['end']:errors.append('day_overflow')
        day_free+=max(0,day['end']-time)
        if task.get('walkBudgetKm') is not None and (not known or day_walking>task['walkBudgetKm']+.05):errors.append('walking_budget')
        travel+=day_travel;visits+=day_visits;free+=day_free;walking+=day_walking;walk_known &= known;day_walk.append(day_walking)
        dm=metrics['days'][di]
        if (dm['travelMinutes'],dm['visitMinutes'],dm['freeMinutes'],dm['finish'])!=(day_travel,day_visits,day_free,time):errors.append('day_metric_mismatch')
    if task.get('origin') and t['days'][0]['activities'][0]['placeId']!=task['origin']:errors.append('origin_missing')
    if task.get('destination') and (not t['days'][-1]['activities'] or t['days'][-1]['activities'][-1]['placeId']!=task['destination']):errors.append('destination_missing')
    if (metrics['travelMinutes'],metrics['visitMinutes'],metrics['freeMinutes'])!=(travel,visits,free):errors.append('total_metric_mismatch')
    if walk_known:
        if metrics['walkingKm'] is None or abs(metrics['walkingKm']-walking)>.005:errors.append('walking_metric_mismatch')
    elif metrics['walkingKm'] is not None:errors.append('unknown_walking_reported_as_known')
    return sorted(set(errors))


def audit_comparison(result,task,route_fixture=False):
    errors=[];signatures=[]
    for option in result['options']:
        errors.extend(audit_option(option,task,route_fixture))
        signatures.append(tuple((d['date'],tuple((a['placeId'],a['duration']) for a in d['activities'])) for d in option['trip']['days']))
    if len(signatures)!=len(set(signatures)):errors.append('duplicate_options')
    if len(signatures)>3:errors.append('too_many_options')
    if result['search']['dayRouteQueries']>task.get('maxRoutes',12):errors.append('route_budget_exceeded')
    if task.get('limited') and not result['search']['limited']:errors.append('missing_limit_state')
    if task['expect']=='generated':
        if len(result['options'])<task.get('minOptions',1):errors.append('feasible_task_rejected')
        if task.get('balanced') and not any([len(d['activities']) for d in o['trip']['days']]==[2,2] for o in result['options']):errors.append('cross_day_repair_missing')
        if task.get('mustChangeOrder') and any(o['includedIds']==task['ids'] for o in result['options']):errors.append('bad_road_order_retained')
    elif result['options']:errors.append('invalid_plan_accepted')
    return sorted(set(errors))

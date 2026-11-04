import pytest
from backend.planning import Requirements,check_order,heuristic
from backend.catalog import normalize

def test_requirements_date_and_deadline():
    with pytest.raises(ValueError):Requirements(startDate='2026-11-31',days=2,people=2)
    with pytest.raises(ValueError):Requirements(startDate='2026-11-14',days=2,people=2,returnBy=1000)
    with pytest.raises(ValueError):Requirements(startDate='2026-11-14',days=2,people=2,start=800,end=700)

def test_candidate_conservation():
    ids=['a','b','c'];pois={'a':{'lng':1,'lat':1},'b':{'lng':2,'lat':1},'c':{'lng':3,'lat':1}}
    assert check_order(heuristic(ids,pois,2),ids,2)
    with pytest.raises(ValueError):check_order([['a','b'],['b']],ids,2)

def test_external_data_bounds_and_identity():
    feature={'properties':{'name':'同名地点','osm_type':'N','osm_id':123},'geometry':{'type':'Point','coordinates':[120.14,30.25]}}
    assert normalize(feature)['coordinateSource']=='https://www.openstreetmap.org/node/123'
    feature['geometry']['coordinates']=[1,1]
    with pytest.raises(ValueError):normalize(feature)

from datetime import date
from backend.requirements import parse_request

def test_one_sentence_keeps_places_and_return_deadline():
    parsed=parse_request('2026-11-14 去杭州两天，两个人，想去西湖和浙工大，喜欢咖啡，少走路，第二天 17:00 前到杭州东站。')
    values=parsed['requirements']
    assert values['startDate']=='2026-11-14' and values['days']==2 and values['people']==2
    assert values['mode']=='walk' and values['pace']=='relaxed' and values['interests']==['咖啡']
    assert values['returnBy']==17*60
    assert [mention['text'] for mention in parsed['mentions']]==['西湖','浙江工业大学','杭州东站']
    assert [mention['role'] for mention in parsed['mentions']]==['visit','visit','destination']
    assert parsed['missing']==[]

def test_missing_date_is_reported_not_guessed():
    parsed=parse_request('周末去杭州玩两天，两个人')
    assert parsed['requirements']['days']==2 and parsed['requirements']['people']==2
    assert 'startDate' in parsed['missing']
    assert parsed['mentions']==[]

def test_short_date_rolls_to_next_year_and_is_flagged():
    parsed=parse_request('3月2日去杭州一天',today=date(2026,11,4))
    assert parsed['requirements']['startDate']=='2027-03-02'
    assert any('未写年份' in note for note in parsed['notes'])

def test_day_trip_and_bare_destination_phrases():
    parsed=parse_request('2026-12-01 杭州一日游，一个人，步行，从杭州东站出发，去西湖')
    assert parsed['requirements']['days']==1 and parsed['requirements']['people']==1
    assert [mention['text'] for mention in parsed['mentions']]==['西湖','杭州东站']
    assert parsed['missing']==[]

def test_soft_pace_without_a_date_is_reported():
    parsed=parse_request('周末去杭州，想喝咖啡，不要太赶')
    assert parsed['requirements']['pace']=='relaxed'
    assert parsed['requirements']['interests']==['咖啡']
    assert set(parsed['missing'])=={'startDate','days','people'}
    assert parsed['mentions']==[]

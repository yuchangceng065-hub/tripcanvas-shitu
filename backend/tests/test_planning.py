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

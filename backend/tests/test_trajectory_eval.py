"""Regression tests for the trajectory evaluator itself.

The oracle must reject a correct plan reached through a bad trajectory, reject
a good trajectory producing a wrong plan, and agree with every human label in
the fixed suite.
"""
import copy
import json
from pathlib import Path

import pytest

from backend.trajectory_eval import (
    SUITE_VERSION, _places_in_args, _places_in_plan, evaluate_suite,
    evaluate_trajectory, validate_suite,
)

ROOT = Path(__file__).resolve().parents[2]
SUITE_FILE = ROOT / 'fixtures' / 'evaluation' / 'trajectory-cases.json'

EXPECT = {
    'terminal': 'completed',
    'allowedTools': ['route_day', 'validate_plan', 'propose_plan'],
    'allowedPlaceIds': ['quyuan', 'museum', 'station'],
    'requiredPlaceIds': ['quyuan', 'museum'],
    'maxSteps': 6,
    'maxToolCalls': 6,
    'expectProposal': True,
    'days': 1,
    'requireValidationBeforeProposal': True,
}
EVENTS = [
    {'seq': 1, 'phase': 'model', 'status': 'ok', 'detail': {'step': 1, 'action': 'route_day'}},
    {'seq': 2, 'phase': 'tool_call', 'status': 'ok',
     'detail': {'step': 1, 'tool': 'route_day', 'args': {'days': [['quyuan', 'museum']]}}},
    {'seq': 3, 'phase': 'tool_result', 'status': 'ok', 'detail': {'step': 1, 'ok': True}},
    {'seq': 4, 'phase': 'model', 'status': 'ok', 'detail': {'step': 2, 'action': 'propose_plan'}},
    {'seq': 5, 'phase': 'tool_call', 'status': 'ok',
     'detail': {'step': 2, 'tool': 'propose_plan', 'args': {'days': [['quyuan', 'museum']], 'summary': 'ok'}}},
    {'seq': 6, 'phase': 'tool_result', 'status': 'ok', 'detail': {'step': 2, 'ok': True}},
    {'seq': 7, 'phase': 'completed', 'status': 'ok', 'detail': {'steps': 8}},
]
RESULT = {'status': 'completed', 'written': False, 'plan': {'days': [['quyuan', 'museum']]}}


def base_case(**overrides):
    case = {'id': 'X', 'title': 'case', 'split': 'regression', 'expectedPass': True,
            'violation': None, 'expect': copy.deepcopy(EXPECT),
            'events': copy.deepcopy(EVENTS), 'result': copy.deepcopy(RESULT)}
    case.update(overrides)
    return case


def loaded_suite():
    from scripts.evaluate_trajectory import materialize
    raw = json.loads(SUITE_FILE.read_text(encoding='utf-8'))
    cases = materialize(raw)
    validate_suite({'suite': raw['suite'], 'cases': cases})
    return cases


# --- Four quadrants ---------------------------------------------------------

def test_clean_run_passes_both_process_and_outcome():
    row = evaluate_trajectory(base_case())
    assert row['verdict'] == 'both_pass' and row['passed']
    assert row['failedChecks'] == [] and row['outcomeReasons'] == []


def test_correct_plan_reached_through_unconfirmed_place_is_a_failure():
    case = base_case(expectedPass=False, violation='place_allowlist')
    case['events'][1]['detail']['args']['days'] = [['quyuan', 'museum', 'silk']]
    row = evaluate_trajectory(case)
    assert row['outcomePass'] is True and row['processPass'] is False
    assert row['verdict'] == 'result_only' and row['passed'] is False
    assert 'place_allowlist' in row['failedChecks']


def test_clean_process_with_missing_required_place_is_a_failure():
    case = base_case(expectedPass=False, violation='missing_required')
    case['result']['plan'] = {'days': [['quyuan']]}
    row = evaluate_trajectory(case)
    assert row['processPass'] is True and row['outcomePass'] is False
    assert row['verdict'] == 'process_only' and row['passed'] is False
    assert any('missing_required' in reason for reason in row['outcomeReasons'])


# --- The fixed suite validates the evaluator --------------------------------

def test_fixed_suite_agrees_with_every_human_label():
    cases = loaded_suite()
    report = evaluate_suite(cases)
    assert report['suite'] == SUITE_VERSION
    assert report['total'] == 14 and report['labeledPass'] == 6
    assert report['agreement'] == report['total'] and report['evaluatorValid'] is True
    assert report['falseAccepts'] == [] and report['falseRejects'] == []
    # Result-only cases are exactly what a result-only evaluator would miss.
    assert report['quadrants']['result_only'] >= 4


@pytest.mark.parametrize('case_id,violation', [
    ('T06', 'place_allowlist'), ('T07', 'missing_required'),
    ('T08', 'no_duplicate_tool_call'), ('T09', 'no_write_on_non_completed_terminal'),
    ('T10', 'step_budget'), ('T11', 'tool_allowlist'),
    ('T12', 'event_sequence_monotonic'),
])
def test_each_injected_violation_is_detected(case_id, violation):
    case = next(item for item in loaded_suite() if item['id'] == case_id)
    row = evaluate_trajectory(case)
    assert row['passed'] is False
    assert violation in row['failedChecks'] or any(violation in reason for reason in row['outcomeReasons'])


# --- Anti-gaming ------------------------------------------------------------

def test_anti_gaming_flags_refusal_inflation():
    refusing = []
    for index in range(10):
        refusing.append(base_case(
            id=f'R{index}', expectedPass=True,
            expect={**EXPECT, 'terminal': 'gave_up', 'expectProposal': False},
            events=[
                {'seq': 1, 'phase': 'model', 'status': 'ok', 'detail': {'step': 1, 'action': 'give_up'}},
                {'seq': 2, 'phase': 'gave_up', 'status': 'ok', 'detail': {}},
            ],
            result={'status': 'gave_up', 'written': False, 'plan': None},
        ))
    report = evaluate_suite(refusing)
    assert report['processPassRate'] == 1.0
    assert report['proposalRate'] == 0.0
    assert report['antiGaming']['suspected'] is True


# --- Suite validation and oracle boundaries ---------------------------------

def test_validate_suite_rejects_broken_suites():
    with pytest.raises(ValueError):
        validate_suite({'suite': SUITE_VERSION, 'cases': []})
    duplicate = [base_case(id='D'), base_case(id='D')]
    with pytest.raises(ValueError):
        validate_suite({'suite': SUITE_VERSION, 'cases': duplicate})
    bad = base_case()
    del bad['expectedPass']
    with pytest.raises(ValueError):
        validate_suite({'suite': SUITE_VERSION, 'cases': [bad]})


def test_free_text_is_never_treated_as_a_place():
    assert _places_in_args({'summary': 'silk 是未确认地点', 'days': [['quyuan', 'museum']]}) == ['quyuan', 'museum']
    assert sorted(_places_in_args({'placeIds': ['quyuan'], 'seedDays': [['museum']]})) == ['museum', 'quyuan']
    assert _places_in_plan({'days': [{'activities': [{'placeId': 'quyuan'}]}]}) == ['quyuan']
    assert _places_in_plan(None) == []


def test_completed_event_is_not_a_confirmation():
    case = base_case(expectedPass=False, violation='write_requires_confirmation')
    case['result']['written'] = True
    row = evaluate_trajectory(case)
    assert 'write_requires_confirmation' in row['failedChecks']
    assert row['verdict'] == 'result_only'


def test_explicit_confirmation_evidence_is_accepted():
    case = base_case()
    case['result']['written'] = True
    case['result']['confirmation'] = {'endpoint': '/planner/confirm',
                                      'baseRevision': 1, 'requestId': 'fixture'}
    row = evaluate_trajectory(case)
    assert row['passed'] is True and row['failedChecks'] == []


def test_validation_before_proposal_requires_a_route_or_validate_tool():
    case = base_case()
    # Only a successful search, no route_day / validate_plan.
    case['events'][1]['detail']['tool'] = 'search_places'
    row = evaluate_trajectory(case)
    assert 'validated_before_proposal' in row['failedChecks']
    case['events'][1]['detail']['tool'] = 'route_day'
    assert 'validated_before_proposal' not in evaluate_trajectory(case)['failedChecks']


def test_trailing_confirmation_event_is_accepted_as_write_evidence():
    case = base_case()
    case['result']['written'] = True
    case['events'].append({'seq': 8, 'phase': 'confirmed', 'status': 'ok',
                           'detail': {'tripId': 't1', 'revision': 1,
                                      'source': 'planner/confirm'}})
    row = evaluate_trajectory(case)
    assert row['passed'] is True, row['failedChecks']
    assert all(check['passed'] for check in row['checks'])


def test_confirmation_event_must_be_single_trailing_on_completed_run():
    marker = {'seq': 8, 'phase': 'confirmed', 'status': 'ok', 'detail': {}}
    # Mid-trace marker, not trailing.
    case = base_case()
    case['result']['written'] = True
    case['events'] = case['events'][:-1] + [marker] + case['events'][-1:]
    assert 'confirmation_event_is_trailing' in evaluate_trajectory(case)['failedChecks']
    # Two markers.
    case = base_case()
    case['result']['written'] = True
    case['events'] += [dict(marker), dict(marker, seq=9)]
    assert 'confirmation_event_is_trailing' in evaluate_trajectory(case)['failedChecks']
    # A confirmation on a run that never completed is not a confirmed write.
    case = base_case()
    case['result']['written'] = True
    case['result']['status'] = 'needs_input'
    case['expect']['terminal'] = 'needs_input'
    case['events'][-1]['phase'] = 'needs_input'
    case['events'].append(dict(marker))
    assert 'confirmation_event_is_trailing' in evaluate_trajectory(case)['failedChecks']

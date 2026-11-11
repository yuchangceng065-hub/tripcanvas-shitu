"""Offline regressions for the desensitized real-task suite and version diff."""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / 'fixtures' / 'evaluation' / 'real-tasks' / 'v1.json'
EVALUATOR = ROOT / 'scripts' / 'evaluate_real_tasks.py'
BUILDER = ROOT / 'scripts' / 'build_real_task_suite.py'


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(value):
    return __import__('hashlib').sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def _run(*args):
    return subprocess.run([sys.executable, str(EVALUATOR), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


def test_suite_is_desensitized_and_expectations_are_frozen():
    text = SUITE.read_text(encoding='utf-8')
    for pattern in (r'capture_[a-z0-9]{8,}', r'https?://',
                     r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
                     r'(?i)(api[_-]?key|password|secret|token)'):
        assert not re.search(pattern, text), pattern
    suite = json.loads(text)
    assert suite['suite'] == 'real-tasks/v1'
    assert {t['category'] for t in suite['tasks']} == {
        'plan_success', 'clarification_resume', 'infeasible_constraint',
        'tool_failure', 'reconnect_recovery'}
    for task in suite['tasks']:
        assert task['expectationSha256'] == _sha(task['spec'])
        capture = task['capture']
        if capture:
            assert capture['specSha256'] == _sha(task['spec'])
            assert task['declaredAt'] <= capture['observedAt']
            assert task['coverage'] == 'captured'
        else:
            assert task['coverage'] == 'not_captured'


def test_builder_strips_nested_secrets_and_pseudonymises_ids():
    mod = _module('build_real_task_suite_test', BUILDER)
    cleaned = mod._clean({
        'ok': True, 'api_key': 'must-not-leak', 'account': 'qa-user',
        'nested': {'password': 'p', 'tool': 'route_day'},
        'tripId': '115b5fee-6031-4054-947d-b33e1d935943',
        'conversation': {'messages': [{'text': 'private'}]},
    })
    assert cleaned['ok'] is True and cleaned['nested'] == {'tool': 'route_day'}
    assert 'api_key' not in cleaned and 'account' not in cleaned
    assert 'conversation' not in cleaned
    assert cleaned['tripId'] == '<id>'


def test_evaluation_keeps_four_families_and_process_outcome_separate():
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / 'report.json'
        result = _run('--version', 'test-v1', '--output', output)
        assert result.returncode == 0, result.stderr
        report = json.loads(output.read_text(encoding='utf-8'))
    assert set(report['families']) == {
        'fixedSyntheticEngineering', 'evaluatorLabelAgreement',
        'syntheticAgentSamples', 'desensitizedRealTasks'}
    assert 'doNotMerge' in report and report['expectationIntegrity'] == 'passed'
    assert report['coverage']['declared'] == 5
    assert report['coverage']['captured'] == 5
    assert set(report['quadrants']) == {'both_pass', 'result_only', 'process_only', 'both_fail'}
    for row in report['tasks']:
        if row['coverage'] == 'captured':
            assert isinstance(row['processPass'], bool) and isinstance(row['outcomePass'], bool)
            assert row['verdict'] in report['quadrants']
        else:
            assert row['verdict'] is None
    by_category = {row['category']: row for row in report['tasks']}
    assert by_category['clarification_resume']['verdict'] == 'both_pass'
    assert by_category['reconnect_recovery']['verdict'] == 'both_pass'
    assert by_category['infeasible_constraint']['verdict'] == 'result_only'
    assert 'injected map outage' in by_category['tool_failure']['source']


def test_tampering_with_pre_run_expectations_is_rejected():
    suite = json.loads(SUITE.read_text(encoding='utf-8'))
    suite['tasks'][0]['spec']['terminal'] = 'gave_up'  # hash no longer matches
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'tampered.json'
        path.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding='utf-8')
        result = _run('--suite', path)
    assert result.returncode == 2
    assert 'expectation hash changed' in result.stderr


def test_version_comparison_reports_improvements_and_regressions():
    mod = _module('evaluate_real_tasks_test', EVALUATOR)
    row = lambda verdict: {'id': 'R01', 'verdict': verdict,  # noqa: E731
                           'processPass': verdict in ('both_pass', 'process_only'),
                           'outcomePass': verdict in ('both_pass', 'result_only')}
    baseline = {'codeVersion': 'v1', 'suiteSha256': 'same',
                'tasks': [row('both_fail'), {**row('both_pass'), 'id': 'R02'}]}
    improved = {'codeVersion': 'v2', 'suiteSha256': 'same',
                'tasks': [row('both_pass'), {**row('both_pass'), 'id': 'R02'}]}
    diff = mod.compare(baseline, improved)
    assert diff['sameSuite'] is True and diff['improved'] == 1 and diff['regressed'] == 0
    assert diff['transitions'][0]['delta'] == 'improved'
    assert diff['transitions'][1]['delta'] == 'unchanged'
    regressed = mod.compare(improved, baseline)
    assert regressed['regressed'] == 1
    assert regressed['transitions'][0]['delta'] == 'regressed'
    assert mod.compare({**baseline, 'suiteSha256': 'other'}, improved)['sameSuite'] is False


def test_two_runs_of_the_same_suite_compare_cleanly():
    with tempfile.TemporaryDirectory() as folder:
        first = Path(folder) / 'v1.json'
        second = Path(folder) / 'v2.json'
        assert _run('--version', 'v1', '--output', first).returncode == 0
        assert _run('--version', 'v2', '--baseline', first,
                    '--output', second).returncode == 0
        report = json.loads(second.read_text(encoding='utf-8'))
    assert report['comparison']['sameSuite'] is True
    assert report['comparison']['baselineVersion'] == 'v1'
    assert report['comparison']['currentVersion'] == 'v2'
    assert all(t['delta'] == 'unchanged' for t in report['comparison']['transitions'])

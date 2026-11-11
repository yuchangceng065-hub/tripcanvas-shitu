#!/usr/bin/env python3
"""Evaluate the desensitized real-task suite and compare two code versions.

Three evaluation families are deliberately kept separate and are NEVER merged
into an "AI success rate":

  1. fixed synthetic engineering cases    -> scripts/evaluate.py
  2. evaluator label agreement           -> scripts/evaluate_trajectory.py (fixed suite)
  3. observed real tasks / model samples -> this script (+ evaluate_agent.py)

Process (`processPass`) and outcome (`outcomePass`) are reported per task and in
a four-quadrant distribution. Pre-run expectations are hashed and checked, so
expectations cannot be quietly rewritten after observing a run.

Usage:
  .venv/bin/python scripts/evaluate_real_tasks.py --output /tmp/real-v1.json --version <commit>
  .venv/bin/python scripts/evaluate_real_tasks.py --baseline /tmp/real-v1.json \
      --version <new-commit> --output /tmp/real-v2.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.trajectory_eval import evaluate_suite  # noqa: E402
from backend.trajectory_export import export_case_from_payload  # noqa: E402

SUITE_FILE = ROOT / 'fixtures' / 'evaluation' / 'real-tasks' / 'v1.json'
FAMILIES = {
    'fixedSyntheticEngineering': {
        'command': '.venv/bin/python scripts/evaluate.py --output-dir /tmp/tripcanvas-evaluation',
        'measures': 'deterministic result checks; 30 fixed tasks',
        'reportedAs': 'passed/total of fixed tasks',
    },
    'evaluatorLabelAgreement': {
        'command': '.venv/bin/python scripts/evaluate_trajectory.py --output /tmp/tripcanvas-trajectory.json',
        'measures': 'does the oracle agree with human labels on 14 synthetic trajectories',
        'reportedAs': 'agreement, falseAccepts, falseRejects',
    },
    'syntheticAgentSamples': {
        'command': '.venv/bin/python scripts/evaluate_agent.py --output /tmp/tripcanvas-agent.json',
        'measures': 'synthetic Agent runs with a stubbed model',
        'reportedAs': 'per-case verdicts, not an accuracy rate',
    },
    'desensitizedRealTasks': {
        'command': '.venv/bin/python scripts/evaluate_real_tasks.py --output /tmp/real-tasks.json',
        'measures': 'observed real runs, process and outcome judged separately',
        'reportedAs': 'four-quadrant verdicts + coverage',
    },
}
VERDICT_RANK = {'both_fail': 0, 'process_only': 1, 'result_only': 2, 'both_pass': 3}


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode()).hexdigest()


def _validate(task: dict) -> list[str]:
    problems = []
    if _sha(task.get('spec')) != task.get('expectationSha256'):
        problems.append('expectation hash changed after declaration')
    capture = task.get('capture')
    if not capture:
        return problems
    if capture.get('specSha256') and capture['specSha256'] != _sha(task['spec']):
        problems.append('capture spec no longer matches the pre-run expectation')
    declared, observed = task.get('declaredAt'), capture.get('observedAt')
    if declared and observed and declared > observed:
        problems.append('expectation declared after the run was captured')
    return problems


def _case(task: dict) -> dict:
    capture = task['capture']
    result = capture['result']
    plan = result.get('plan')
    payload = {
        'events': capture['events'],
        'status': result.get('status'),
        'kind': 'observed',
        'validation': ({'trip': {'days': plan.get('days')}}
                       if isinstance(plan, dict) and isinstance(plan.get('days'), list)
                       else None),
    }
    case = export_case_from_payload(capture['runAlias'], payload, task['spec'])
    case['id'] = task['id']
    case['title'] = task['title']
    case['result']['written'] = bool(result.get('written'))
    case['result']['confirmation'] = result.get('confirmation')
    case['result']['evidence'] = capture.get('evidence', {})
    for key in ('written', 'requireResume', 'requireSseReplay', 'requiredIssueCode',
                'expectedMcpFailures'):
        if key in task['spec']:
            case['expect'][key] = task['spec'][key]
    return case


def compare(baseline: dict, current: dict) -> dict:
    before = {row['id']: row for row in baseline.get('tasks', [])}
    after = {row['id']: row for row in current.get('tasks', [])}
    transitions = []
    for task_id in sorted(set(before) | set(after)):
        old, new = before.get(task_id), after.get(task_id)
        if old is None:
            delta = 'new'
        elif new is None:
            delta = 'removed'
        elif old.get('verdict') == new.get('verdict'):
            delta = 'unchanged'
        else:
            delta = ('improved' if VERDICT_RANK.get(new['verdict'], 0)
                    > VERDICT_RANK.get(old['verdict'], 0) else 'regressed')
        transitions.append({
            'id': task_id,
            'baselineVerdict': old.get('verdict') if old else None,
            'currentVerdict': new.get('verdict') if new else None,
            'delta': delta,
            'processPass': {'before': old.get('processPass') if old else None,
                           'after': new.get('processPass') if new else None},
            'outcomePass': {'before': old.get('outcomePass') if old else None,
                           'after': new.get('outcomePass') if new else None},
        })
    return {
        'baselineVersion': baseline.get('codeVersion'),
        'currentVersion': current.get('codeVersion'),
        'sameSuite': baseline.get('suiteSha256') == current.get('suiteSha256'),
        'baselineSuiteSha256': baseline.get('suiteSha256'),
        'currentSuiteSha256': current.get('suiteSha256'),
        'transitions': transitions,
        'improved': sum(t['delta'] == 'improved' for t in transitions),
        'regressed': sum(t['delta'] == 'regressed' for t in transitions),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=SUITE_FILE)
    parser.add_argument('--output', type=Path, help='write the full JSON report here')
    parser.add_argument('--baseline', type=Path, help='previous report to compare against')
    parser.add_argument('--version', default='unspecified', help='label of the code under test')
    parser.add_argument('--fail-on-regression', action='store_true',
                        help='exit 1 when a compared task regressed')
    args = parser.parse_args()

    raw_bytes = args.suite.read_bytes()
    suite = json.loads(raw_bytes)
    if suite.get('suite') != 'real-tasks/v1':
        parser.error('unknown real-task suite version')
    problems = {task['id']: _validate(task) for task in suite['tasks']}
    problems = {key: value for key, value in problems.items() if value}
    if problems:
        print('pre-run expectation integrity failed:', file=sys.stderr)
        for key, issues in problems.items():
            print(f'  {key}: {", ".join(issues)}', file=sys.stderr)
        return 2

    captured = [t for t in suite['tasks'] if t.get('capture')]
    cases = [_case(t) for t in captured]
    verdicts = evaluate_suite(cases, labeled=False) if cases else {'cases': [], 'quadrants': {}}
    by_id = {row['id']: row for row in verdicts['cases']}

    tasks = []
    for task in suite['tasks']:
        row = by_id.get(task['id'])
        tasks.append({
            'id': task['id'], 'category': task['category'], 'title': task['title'],
            'coverage': task['coverage'],
            'expectationSha256': task['expectationSha256'],
            'verdict': row['verdict'] if row else None,
            'processPass': row['processPass'] if row else None,
            'outcomePass': row['outcomePass'] if row else None,
            'failedChecks': row['failedChecks'] if row else [],
            'outcomeReasons': row.get('outcomeReasons', []) if row else [],
            'eventCount': task['capture']['eventCount'] if task.get('capture') else 0,
            'source': task['capture']['source'] if task.get('capture') else None,
        })
    report = {
        'kind': 'real-task-evaluation/v1',
        'suite': suite['suite'],
        'suiteSha256': hashlib.sha256(raw_bytes).hexdigest(),
        'codeVersion': args.version,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(),
        'families': FAMILIES,
        'doNotMerge': ('These families measure different things. Never combine their numbers '
                        'into a single AI success rate.'),
        'coverage': {
            'declared': len(suite['tasks']),
            'captured': len(captured),
            'missing': [t['category'] for t in suite['tasks'] if not t.get('capture')],
            'hint': 'a declared task without a capture is a coverage gap, not a failure',
        },
        'expectationIntegrity': 'passed',
        'processPass': verdicts.get('processPass', 0),
        'outcomePass': verdicts.get('outcomePass', 0),
        'quadrants': verdicts.get('quadrants', {}),
        'processPassRate': verdicts.get('processPassRate', 0),
        'outcomePassRate': verdicts.get('outcomePassRate', 0),
        'tasks': tasks,
        'note': ('Observed runs with per-task provenance. Process and outcome are judged separately; this is a '
                 'behaviour measurement, not evaluator label agreement or an AI accuracy rate.'),
    }
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        report['comparison'] = compare(baseline, report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"suite        {report['suite']}  sha256={report['suiteSha256'][:16]}")
    print(f"version      {report['codeVersion']}")
    print(f"coverage     declared={report['coverage']['declared']}  captured={report['coverage']['captured']}"
          f"  missing={report['coverage']['missing']}")
    print(f"separated    processPass={report['processPass']}  outcomePass={report['outcomePass']}"
          f"  quadrants={report['quadrants']}")
    for row in tasks:
        print(f"  {row['id']} {row['category']:<24} {str(row['verdict']):<12} "
              f"failed={','.join(row['failedChecks']) or '-'}")
    if 'comparison' in report:
        c = report['comparison']
        print(f"comparison   {c['baselineVersion']} -> {c['currentVersion']}  "
              f"improved={c['improved']}  regressed={c['regressed']}  sameSuite={c['sameSuite']}")
        for t in c['transitions']:
            print(f"  {t['id']} {t['baselineVerdict']} -> {t['currentVerdict']}  {t['delta']}")
        if args.fail_on_regression and c['regressed']:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

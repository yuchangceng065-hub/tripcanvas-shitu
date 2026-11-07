#!/usr/bin/env python3
"""Trajectory evaluation CLI.

Modes:
  * default        - 14 fixed synthetic traces, checking the evaluator itself.
  * --from-spec    - real persisted runs read from a local DB (DATABASE_URL).
  * --from-capture - one or more live API run payloads captured by
                     scripts/capture_live_run.py, no local DB required.

All modes are offline at evaluation time: no model, network or external calls.
"""
import argparse
import copy
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.trajectory_eval import evaluate_suite, validate_suite  # noqa: E402
from backend.trajectory_export import (  # noqa: E402
    export_case_from_payload, export_cases, spec_template,
)

SUITE_FILE = ROOT / 'fixtures' / 'evaluation' / 'trajectory-cases.json'


def materialize(raw: dict) -> list[dict]:
    defaults = raw.get('defaults')
    if not isinstance(defaults, dict):
        raise ValueError('suite defaults are missing')
    cases = []
    for item in raw['cases']:
        case = {
            'id': item['id'],
            'title': item['title'],
            'split': item['split'],
            'expectedPass': item['expectedPass'],
            'violation': item.get('violation'),
            'events': copy.deepcopy(item.get('events', defaults['events'])),
            'result': copy.deepcopy(item.get('result', defaults['result'])),
            'expect': {**defaults['expect'], **item.get('expect', {})},
        }
        cases.append(case)
    return cases


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finalize(report: dict, args) -> dict:
    report['python'] = platform.python_version()
    report['generatedAt'] = datetime.now(timezone.utc).isoformat()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def _print_observed(report):
    print(f"mode         {report['mode']}  source={report.get('source', 'db')}")
    print(f"runs         {report['total']}  passed={report['passed']}")
    print(f"verdicts     {report['quadrants']}")
    print(f"rates        processPass={report['processPassRate']}  proposal={report['proposalRate']}  refusal={report['refusalRate']}")
    for row in report['cases']:
        print(f"  {row['id']:<18} {row['verdict']:<12} "
              f"failed={','.join(row['failedChecks']) or '-'} "
              f"outcome={','.join(row['outcomeReasons']) or '-'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=SUITE_FILE)
    parser.add_argument('--output', type=Path, help='write the full JSON report here')
    parser.add_argument('--case', help='replay a single fixed case id')
    parser.add_argument('--from-spec', type=Path,
                        help='JSON with defaults + runs; export real persisted runs')
    parser.add_argument('--from-capture', type=Path,
                        help='capture JSON produced by scripts/capture_live_run.py')
    parser.add_argument('--spec-template', type=Path,
                        help='write a spec template for --from-spec and exit')
    parser.add_argument('--fail-on-violation', action='store_true',
                        help='observed mode: exit 1 when any real run is judged failing')
    args = parser.parse_args()

    if args.spec_template:
        args.spec_template.parent.mkdir(parents=True, exist_ok=True)
        args.spec_template.write_text(
            json.dumps(spec_template(), ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'spec template written to {args.spec_template}')
        return 0

    if args.from_capture:
        raw = json.loads(args.from_capture.read_text(encoding='utf-8'))
        if raw.get('captureVersion') != 'live-run/v1':
            parser.error('unknown capture version')
        cases = [export_case_from_payload(item['runId'], item['payload'], item['spec'])
                 for item in raw.get('runs', [])]
        if not cases:
            parser.error('capture contains no runs')
        report = evaluate_suite(cases, labeled=False)
        report['source'] = 'live-api-payload'
        report['captureFile'] = str(args.from_capture)
        report['captureSha256'] = _sha256(args.from_capture)
        report['captureMetadata'] = {key: value for key, value in raw.items() if key != 'runs'}
        report['observedRuns'] = [{
            'runId': item['runId'], 'observedStatus': item.get('observedStatus'),
            'eventCount': item.get('observedEventCount'),
            'phases': item.get('observedPhases'), 'written': item.get('written'),
            'expectedTerminal': item['spec']['terminal'],
        } for item in raw['runs']]
        report['note'] = ('Observed live runs. Expectations were declared before capture; '
                          'written status was checked against the trips API.')
        _finalize(report, args)
        _print_observed(report)
        violations = report['total'] - report['passed']
        return 1 if (args.fail_on_violation and violations) else 0

    if args.from_spec:
        raw = json.loads(args.from_spec.read_text(encoding='utf-8'))
        runs = {key: value for key, value in raw.get('runs', {}).items()
                if not key.startswith('<')}
        if not runs:
            parser.error('--from-spec contains no run ids')
        spec_map = {'': raw.get('defaults', {})}
        spec_map.update(runs)
        cases = export_cases(spec_map)
        report = evaluate_suite(cases, labeled=False)
        report['source'] = 'database'
        report['specFile'] = str(args.from_spec)
        report['specSha256'] = _sha256(args.from_spec)
        report['note'] = ('Observed real runs. Expectations come from the caller\'s pre-run inputs; '
                          'this is a behaviour measurement, not an evaluator self-check.')
        report['provenance'] = [case['provenance'] for case in cases]
        _finalize(report, args)
        _print_observed(report)
        violations = report['total'] - report['passed']
        return 1 if (args.fail_on_violation and violations) else 0

    raw_bytes = args.suite.read_bytes()
    raw = json.loads(raw_bytes)
    cases = materialize(raw)
    validate_suite({'suite': raw['suite'], 'cases': cases})
    if args.case:
        cases = [case for case in cases if case['id'] == args.case]
        if not cases:
            parser.error(f'unknown case {args.case}')

    report = evaluate_suite(cases, labeled=True)
    report['suiteFile'] = str(args.suite)
    report['suiteSha256'] = hashlib.sha256(raw_bytes).hexdigest()
    _finalize(report, args)

    print(f"suite        {report['suite']}  sha256={report['suiteSha256'][:16]}")
    print(f"cases        {report['total']}  labelled-pass={report['labeledPass']}  negative={report['negativeCases']}")
    print(f"verdicts     {report['quadrants']}")
    print(f"passed       {report['passed']}   process={report['processPass']}  outcome={report['outcomePass']}")
    print(f"evaluator    agreement={report['agreement']}/{report['total']}  valid={report['evaluatorValid']}")
    print(f"falseAccepts {report['falseAccepts']}  falseRejects {report['falseRejects']}")
    print(f"rates        processPass={report['processPassRate']}  proposal={report['proposalRate']}  refusal={report['refusalRate']}")
    print(f"antiGaming   suspected={report['antiGaming']['suspected']}")
    if not args.output:
        print('note         use --output to keep the full per-check report')
    return 0 if report['evaluatorValid'] else 1


if __name__ == '__main__':
    sys.exit(main())

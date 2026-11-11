#!/usr/bin/env python3
"""Build the desensitized, versioned real-task suite from live captures.

`scripts/capture_live_run.py` writes raw captures that contain QA account
names, API base URLs and UUIDs. This builder keeps only the trajectory facts the
oracle reads (event phases/status/details, final status, produced plan, and the
explicit write evidence) and replaces identifiers with stable aliases. The
pre-run `spec` is copied verbatim: expectations must never be derived from the
observed run.

Usage:
  .venv/bin/python scripts/build_real_task_suite.py \
      --capture /path/to/one.json --category plan_success \
      --capture /path/to/two.json --category clarification_resume \
      --output fixtures/evaluation/real-tasks/v1.json

Categories without a capture are still declared with their pre-run
expectations so the suite has a stable shape; they are reported as
`not_captured` coverage gaps, never filled with invented traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.trajectory_export import DEFAULT_ALLOWED_TOOLS  # noqa: E402

CATEGORIES = {
    'plan_success': ('规划成功', 'completed', True, True, True, ['quyuan', 'museum', 'station']),
    'clarification_resume': ('反问续接', 'needs_confirmation', False, False, False, ['quyuan', 'amap:B023B0253E']),
    'infeasible_constraint': ('不可行约束', 'gave_up', False, False, False, ['quyuan', 'station']),
    'tool_failure': ('工具失败', 'gave_up', False, False, False, ['quyuan', 'museum', 'station']),
    'reconnect_recovery': ('断线恢复', 'needs_input', False, False, False, ['quyuan', 'station']),
}
DEFAULT_CAPTURE_DIR = ROOT.parent
# Fixed on purpose: rebuilding from the same captures must produce the same
# suite hash, otherwise --baseline comparisons would look like regressions.
SUITE_DECLARED_AT = '2026-11-07T00:00:00+00:00'
KNOWN_CAPTURES = {
    'plan_success': DEFAULT_CAPTURE_DIR / '拾途-线上完整链路采集.json',
    'clarification_resume': DEFAULT_CAPTURE_DIR / '拾途-线上真实运行采集.json',
}
SECRET_KEYS = re.compile(
    r'(token|secret|password|passwd|api[_-]?key|authorization|cookie|credential|'
    r'account|api[_-]?base|email|phone|session[_-]?id|access[_-]?token)', re.I)
UUID_RE = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode()).hexdigest()


def _clean(value):
    """Recursively drop secret-bearing keys and pseudonymise UUIDs."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()
                if not SECRET_KEYS.search(str(k)) and str(k) not in
                ('conversation', 'modelUsage', 'usageReport', 'usageSnapshot')}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return UUID_RE.sub('<id>', value)
    return value


def _alias(text: str) -> str:
    return 'live-' + hashlib.sha256(text.encode()).hexdigest()[:10]


def declared_spec(category: str) -> dict:
    label, terminal, expect_proposal, written, validation, places = CATEGORIES[category]
    spec = {
        'title': label,
        'terminal': terminal,
        'allowedTools': DEFAULT_ALLOWED_TOOLS,
        'allowedPlaceIds': places,
        'allowedPlaceIdsSource': 'place mentions / confirmed candidates in the pre-run request',
        'requiredPlaceIds': ['quyuan', 'museum'] if expect_proposal else [],
        'expectProposal': expect_proposal,
        'requireValidationBeforeProposal': validation,
        'maxSteps': 6,
        'maxToolCalls': 6,
        'written': written,
    }
    if expect_proposal:
        spec['days'] = 1
    if category == 'clarification_resume':
        spec['requireResume'] = True
    if category == 'reconnect_recovery':
        spec['requireSseReplay'] = True
    if category in ('infeasible_constraint', 'tool_failure'):
        spec['expectedMcpFailures'] = ['validate_plan']
    if category == 'infeasible_constraint':
        spec['requiredIssueCode'] = 'day_overflow'
    return spec


def capture_from_file(path: Path, category: str) -> dict:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw.get('captureVersion') != 'live-run/v1':
        raise ValueError(f'{path}: unknown capture version')
    item = raw['runs'][0]
    spec = dict(item['spec'])
    payload = item['payload']
    events = [{'seq': e.get('seq'), 'phase': e.get('phase'),
               'status': e.get('status', 'ok'),
               'detail': _clean(e.get('detail') if isinstance(e.get('detail'), dict) else {})}
              for e in payload.get('events', [])]
    validation = payload.get('validation')
    plan = None
    if isinstance(validation, dict) and isinstance(validation.get('trip'), dict):
        if validation['trip'].get('days'):
            plan = {'days': _clean(validation['trip']['days'])}
    result = {
        'status': payload.get('status'),
        'written': bool(item.get('written')),
        'plan': plan,
        'confirmation': _clean(spec.get('confirmation')),
    }
    return {
        'declaredAt': raw.get('declaredAt', raw.get('capturedAt')),
        'observedAt': raw.get('capturedAt'),
        'source': _clean(raw.get('source', 'live API payload, desensitized')),
        'release': (raw.get('healthAtCapture') or {}).get('commit'),
        'runAlias': _alias(str(item['runId'])),
        'eventCount': len(events),
        'events': events,
        'result': result,
        'evidence': _clean(item.get('evidence', {})),
        'spec': _clean(spec),
        'specSha256': _sha(_clean(spec)),
    }


def build(output: Path, captures: dict[str, Path]) -> dict:
    tasks = []
    for order, (category, (label, *_rest)) in enumerate(CATEGORIES.items(), start=1):
        spec = declared_spec(category)
        captured = None
        if category in captures:
            captured = capture_from_file(captures[category], category)
            # A capture's own pre-run spec wins: it was written before the run.
            spec = captured['spec']
        tasks.append({
            'id': f'R{order:02d}',
            'category': category,
            'title': label,
            'declaredAt': captured['declaredAt'] if captured else SUITE_DECLARED_AT,
            'expectationSource': 'pre-run request inputs (never derived from the observed result)',
            'spec': spec,
            'expectationSha256': _sha(spec),
            'coverage': 'captured' if captured else 'not_captured',
            # Keep the pre-run spec hash in the capture: it proves the declared
            # expectations were not edited after observing the run.
            'capture': ({k: v for k, v in captured.items() if k != 'spec'}
                       if captured else None),
            'captureHint': (None if captured else
                            'scripts/capture_live_run.py --mode full plus an explicit pre-run spec'),
        })
    suite = {
        'suite': 'real-tasks/v1',
        'description': ('Desensitized Agent executions from live and isolated environments. Expectations precede the '
                         'run; process and outcome are evaluated separately. Version comparison '
                         'replays this same suite against two code versions.'),
        'declaredAt': SUITE_DECLARED_AT,
        'familiesAreSeparate': True,
        'tasks': tasks,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding='utf-8')
    return suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'fixtures' / 'evaluation' / 'real-tasks' / 'v1.json')
    parser.add_argument('--capture', action='append', nargs=2,
                        metavar=('CATEGORY', 'FILE'),
                        help='attach a desensitized live capture to a category')
    parser.add_argument('--use-known-captures', action='store_true',
                        help='attach the two captures shipped with this workspace')
    args = parser.parse_args()
    captures = {}
    for category, file in (args.capture or []):
        if category not in CATEGORIES:
            parser.error(f'unknown category {category}')
        captures[category] = Path(file)
    if args.use_known_captures:
        for category, path in KNOWN_CAPTURES.items():
            if path.exists():
                captures.setdefault(category, path)
    suite = build(args.output, captures)
    print(f"suite        {suite['suite']} -> {args.output}")
    for task in suite['tasks']:
        print(f"  {task['id']} {task['category']:<24} {task['coverage']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Run the real LangGraph/MCP API against synthetic inputs using LangSmith's SDK."""
import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline', type=Path, help='Compare with a prior synthetic evaluation report')
    parser.add_argument('--capture-tool-failure', type=Path,
                        help='Record the isolated tool-failure execution for the real-task suite')
    parser.add_argument('--upload', action='store_true', help='Upload synthetic examples and results to LangSmith')
    args = parser.parse_args()
    if args.upload and not os.getenv('LANGSMITH_API_KEY'):
        parser.error('--upload requires LANGSMITH_API_KEY in the environment')

    from langsmith import Client
    from langsmith.evaluation import evaluate
    from langsmith.schemas import Example

    with tempfile.TemporaryDirectory(prefix='tripcanvas-agent-eval-') as directory:
        os.environ.update(DATABASE_URL=f'sqlite:///{directory}/eval.db', PLANNER_MODE='rules')
        for key in ('AMAP_WEB_KEY', 'AMAP_JS_KEY', 'AMAP_JS_SECURITY_CODE', 'MODEL_API_KEY', 'GROQ_API_KEY'):
            os.environ.pop(key, None)
            os.environ[key] = ''
        from scripts.agent_cases import CASES, run_case

        if args.capture_tool_failure:
            from scripts.build_real_task_suite import declared_spec

            spec = declared_spec('tool_failure')
            declared = datetime.now(timezone.utc).isoformat()
            failure = run_case('upstream_unavailable', include_observation=True)
            if failure['errors']:
                raise RuntimeError(failure['errors'])
            capture = {
                'captureVersion': 'live-run/v1',
                'source': 'isolated API execution, scripted model and injected map outage',
                'declaredAt': declared,
                'capturedAt': datetime.now(timezone.utc).isoformat(),
                'runs': [{**failure['observation'], 'spec': spec,
                          'evidence': {'injectedHttpStatus': 503,
                                       'routeFixtureCalls': failure['roadFixtureCalls']}}],
            }
            args.capture_tool_failure.parent.mkdir(parents=True, exist_ok=True)
            args.capture_tool_failure.write_text(
                json.dumps(capture, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
            )
            return 0

        rows = [{'inputs': {'case_id': name}, 'outputs': {'terminal': case['terminal']}}
                for name, case in CASES.items()]
        client = None
        if args.upload:
            client = Client()
            dataset = client.create_dataset(
                'TripCanvas synthetic Agent QA ' + uuid.uuid4().hex[:8],
                description='Synthetic model decisions and roads, no real user data.',
            )
            client.create_examples(dataset_id=dataset.id, examples=rows)
            data = dataset.name
        else:
            data = [Example(id=uuid.uuid5(uuid.NAMESPACE_URL, 'tripcanvas:' + row['inputs']['case_id']), **row)
                    for row in rows]

        def target(inputs):
            return run_case(inputs['case_id'])

        def outcome(outputs, reference_outputs):
            passed = outputs.get('status') == reference_outputs['terminal'] and outputs.get('errors') == []
            return {'key': 'verified_outcome', 'score': int(passed)}

        experiment = evaluate(
            target, data=data, evaluators=[outcome], max_concurrency=0, client=client,
            upload_results=args.upload, experiment_prefix='tripcanvas-agent',
            metadata={'fixtureSuite': 'tripcanvas-agent-v1', 'model': 'scripted', 'roads': 'synthetic'},
            disable_evaluator_tracing=True,
        )
        results = []
        for row in experiment:
            run = row['run']
            evaluation = row['evaluation_results']['results'][0]
            results.append({**(run.outputs or {'case': row['example'].inputs['case_id']}),
                            'passed': evaluation.score == 1})
        report = {'suite': 'tripcanvas-agent-v1', 'mode': 'synthetic', 'uploaded': args.upload,
                  'passed': sum(row['passed'] for row in results), 'total': len(results), 'results': results}
        if args.baseline:
            previous = json.loads(args.baseline.read_text())
            if previous.get('suite') != report['suite']:
                parser.error('--baseline must use the same fixture suite')
            old = {row['case']: row for row in previous['results']}
            report['comparison'] = [
                {'case': row['case'], 'beforePassed': old[row['case']]['passed'], 'afterPassed': row['passed'],
                 'roadCallDelta': row['roadFixtureCalls'] - old[row['case']]['roadFixtureCalls']}
                for row in results if row['case'] in old and 'roadFixtureCalls' in row and 'roadFixtureCalls' in old[row['case']]
            ]
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + '\n')
        print(output)
        return int(report['passed'] != report['total'] or report['total'] != len(CASES))


if __name__ == '__main__':
    raise SystemExit(main())

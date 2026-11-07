#!/usr/bin/env python3
"""Offline evaluation: retrieval quality, abstention, cache and latency.

No model or network calls. Run from anywhere:
    python labs/rag-qa/evaluate.py
    python labs/rag-qa/evaluate.py --json /tmp/rag-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rag.cache import MemoryBackend, SemanticCache  # noqa: E402
from rag.embeddings import HashingTfidfEmbedder  # noqa: E402
from rag.generation import ExtractiveGenerator  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402

CORPUS = ROOT / 'data' / 'corpus.json'
QUESTIONS = ROOT / 'data' / 'questions.json'


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
    return ordered[index]


def run() -> dict:
    cases = json.loads(QUESTIONS.read_text(encoding='utf-8'))
    pipeline = RAGPipeline(
        embedder=HashingTfidfEmbedder(),
        generator=ExtractiveGenerator(),
        cache=SemanticCache(MemoryBackend(), base_ttl_ms=60_000, null_ttl_ms=5_000),
    )
    index_ms = pipeline.index(CORPUS)

    hit1 = hit3 = reciprocal = 0
    answered, correct_abstain, wrong_answer = 0, 0, 0
    latencies: list[float] = []
    failures: list[str] = []
    per_case = []

    for case in cases:
        query, expected = case['question'], case['expected_docs']
        answer, trace = pipeline.ask(query, use_cache=False)
        latencies.append(trace.latency_ms)
        docs = answer.sources
        rank = next((i + 1 for i, d in enumerate(docs) if d in expected), 0) if expected else 0

        if expected:
            if rank == 1:
                hit1 += 1
            if 1 <= rank <= 3:
                hit3 += 1
            reciprocal += 1 / rank if rank else 0
            if rank:
                answered += 1
            else:
                failures.append(f"{case['id']}: expected {expected}, got {docs} / refused={answer.refused}")
        else:
            # A negative case passes only when the system abstains.
            if answer.refused:
                correct_abstain += 1
            else:
                wrong_answer += 1
                failures.append(f"{case['id']}: should abstain, got {docs}")

        per_case.append({'id': case['id'], 'expected': expected,
                         'citations': answer.citations, 'refused': answer.refused,
                         'reason': answer.reason, 'latencyMs': round(trace.latency_ms, 3)})

    positive = sum(1 for c in cases if c['expected_docs'])
    negative = len(cases) - positive

    # Cache behaviour: repeat every question, all should be served from cache.
    before = pipeline.cache.stats()['recomputations']
    for case in cases:
        pipeline.ask(case['question'])
    recomputed = pipeline.cache.stats()['recomputations'] - before

    report = {
        'suite': 'rag-qa/v1',
        'cases': len(cases), 'positive': positive, 'negative': negative,
        'chunks': len(pipeline.chunks), 'indexMs': round(index_ms, 2),
        'hitAt1': round(hit1 / positive, 3),
        'hitAt3': round(hit3 / positive, 3),
        'mrr': round(reciprocal / positive, 3),
        'abstainAccuracy': round(correct_abstain / negative, 3),
        'wrongAnswersOnNegative': wrong_answer,
        'cache': {**pipeline.cache.stats(),
                  'repeatRecomputations': recomputed,
                  'repeatCount': len(cases)},
        'latencyMs': {'p50': round(percentile(latencies, 0.5), 3),
                      'p95': round(percentile(latencies, 0.95), 3),
                      'mean': round(statistics.mean(latencies), 3)},
        'failures': failures,
        'perCase': per_case,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='write the full report here')
    parser.add_argument('--fail-on-regression', action='store_true',
                        help='exit 1 when quality gates are not met')
    args = parser.parse_args()
    report = run()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"cases      {report['cases']}  (positive={report['positive']} negative={report['negative']})")
    print(f"chunks     {report['chunks']}  index={report['indexMs']} ms")
    print(f"retrieval  Hit@1={report['hitAt1']}  Hit@3={report['hitAt3']}  MRR={report['mrr']}")
    print(f"abstain    accuracy={report['abstainAccuracy']}  wrongAnswers={report['wrongAnswersOnNegative']}")
    print(f"cache      hits={report['cache']['hits']} misses={report['cache']['misses']} "
          f"repeatRecomputations={report['cache']['repeatRecomputations']}/{report['cache']['repeatCount']}")
    print(f"latency    p50={report['latencyMs']['p50']} ms  p95={report['latencyMs']['p95']} ms")
    for failure in report['failures']:
        print('  FAIL', failure)
    if args.fail_on_regression:
        ok = (report['hitAt3'] >= 0.8 and report['mrr'] >= 0.6
              and report['abstainAccuracy'] == 1.0
              and report['cache']['repeatRecomputations'] == 0)
        print('gate      ', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

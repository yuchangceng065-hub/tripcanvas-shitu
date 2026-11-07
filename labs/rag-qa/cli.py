#!/usr/bin/env python3
"""Interactive / one-shot CLI for the RAG lab.

    python labs/rag-qa/cli.py "曲院风荷什么时候看荷花最好"
    python labs/rag-qa/cli.py --interactive
    python labs/rag-qa/cli.py "西湖..." --no-cache --trace
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from rag.pipeline import RAGPipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query', nargs='?', help='one question; omit for --interactive')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--no-cache', action='store_true')
    parser.add_argument('--trace', action='store_true', help='print the per-stage trace')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    pipeline = RAGPipeline()
    pipeline.index(ROOT / 'data' / 'corpus.json')

    def answer(query: str) -> None:
        result, trace = pipeline.ask(query, use_cache=not args.no_cache)
        if args.json:
            print(json.dumps({'query': query, 'answer': result.text,
                              'citations': result.citations, 'sources': result.sources,
                              'refused': result.refused, 'reason': result.reason,
                              'fromCache': result.from_cache,
                              'trace': trace.as_dict()}, ensure_ascii=False, indent=2))
            return
        print(f'\nQ: {query}')
        if result.refused:
            print('A: [拒答] 知识库中没有足够依据回答这个问题。'
                  f'（reason={result.reason}）')
        else:
            print(f'A: {result.text}')
            print('来源：' + '、'.join(result.citations))
            print(f'文档：{", ".join(result.sources)}'
                  + ('  [缓存命中]' if result.from_cache else ''))
        if args.trace:
            print('trace: ' + json.dumps(trace.as_dict(), ensure_ascii=False))

    if args.query:
        answer(args.query)
    elif args.interactive:
        print('RAG Lab · 输入问题，空行退出。')
        while True:
            try:
                line = input('> ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            answer(line)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""End-to-end orchestration with a per-step trace.

index(): docs -> chunks -> embeddings -> vector store
ask()  : query -> cache -> embed -> retrieve -> rerank -> budget -> generate
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cache import SemanticCache, cache_key, redis_backend_from_env
from .chunking import Chunk, chunk_corpus, load_documents
from .embeddings import Embedder, default_embedder
from .generation import Draft, Generator, default_generator
from .rerank import context_budget, rerank
from .vectorstore import Hit, VectorStore


@dataclass
class Answer:
    query: str
    text: str
    citations: list[str]
    refused: bool
    reason: str = ''
    from_cache: bool = False
    sources: list[str] = field(default_factory=list)  # doc_ids, ordered, deduped


@dataclass
class Trace:
    stages: list[dict[str, Any]] = field(default_factory=list)
    from_cache: bool = False
    latency_ms: float = 0.0
    retrieved: int = 0
    reranked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {'stages': self.stages, 'from_cache': self.from_cache,
                'latencyMs': round(self.latency_ms, 3),
                'retrieved': self.retrieved, 'reranked': self.reranked}


class RAGPipeline:
    def __init__(self, embedder: Embedder | None = None,
                 generator: Generator | None = None,
                 cache: SemanticCache | None = None,
                 chunk_size: int = 90, chunk_overlap: int = 25,
                 top_k: int = 8, top_n: int = 3, max_context_chars: int = 900):
        self.embedder = embedder or default_embedder()
        self.generator = generator or default_generator()
        self.cache = cache or SemanticCache(redis_backend_from_env())
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k
        self.top_n = top_n
        self.max_context_chars = max_context_chars
        self.store = VectorStore()
        self.chunks: list[Chunk] = []

    def index(self, corpus_path: str | Path) -> int:
        started = time.perf_counter()
        docs = load_documents(corpus_path)
        self.chunks = chunk_corpus(docs, size=self.chunk_size,
                                   overlap=self.chunk_overlap)
        texts = [c.text for c in self.chunks]
        self.embedder.fit(texts)
        vectors = [self.embedder.encode(text) for text in texts]
        self.store = VectorStore()
        self.store.add(self.chunks, vectors)
        self._index_ms = (time.perf_counter() - started) * 1000
        return len(self.chunks)

    def _compute(self, query: str) -> dict:
        started = time.perf_counter()
        stages: list[dict[str, Any]] = []

        vector = self.embedder.encode(query)
        stages.append({'stage': 'embed', 'dimension': len(vector),
                       'ms': round((time.perf_counter() - started) * 1000, 3)})
        mark = time.perf_counter()
        hits: list[Hit] = self.store.search(vector, top_k=self.top_k)
        stages.append({'stage': 'retrieve', 'count': len(hits),
                       'ms': round((time.perf_counter() - mark) * 1000, 3)})
        mark = time.perf_counter()
        reranked = rerank(query, hits, top_n=self.top_n)
        stages.append({'stage': 'rerank', 'count': len(reranked),
                       'ms': round((time.perf_counter() - mark) * 1000, 3)})
        mark = time.perf_counter()
        kept = context_budget(reranked, self.max_context_chars)
        draft: Draft = self.generator.generate(query, kept)
        stages.append({'stage': 'generate', 'refused': draft.refused,
                       'reason': draft.reason,
                       'ms': round((time.perf_counter() - mark) * 1000, 3)})

        sources = draft.sources
        payload = {
            'text': draft.text, 'citations': draft.citations,
            'sources': sources,
            'refused': draft.refused, 'reason': draft.reason,
            'trace': {'stages': stages, 'retrieved': len(hits),
                      'reranked': len(reranked),
                      'latencyMs': round((time.perf_counter() - started) * 1000, 3)},
        }
        # A refused answer is a legitimate NULL result: cache it briefly to
        # stop repeated misses from hammering the retriever.
        return payload if not draft.refused else None

    def ask(self, query: str, use_cache: bool = True) -> tuple[Answer, Trace]:
        if not self.chunks:
            raise RuntimeError('index() must be called before ask()')
        key = cache_key(query, self.top_k, self.top_n)
        started = time.perf_counter()
        payload, hit = self.cache.get_or_compute(
            key, lambda: self._compute(query), force=not use_cache)

        trace = Trace(from_cache=bool(hit and payload is not None),
                      latency_ms=(time.perf_counter() - started) * 1000)
        if payload is None:
            return (Answer(query=query, text='', citations=[], refused=True,
                           reason='no_evidence', from_cache=bool(hit)), trace)

        inner = payload['trace']
        trace.stages = inner['stages']
        trace.retrieved = inner['retrieved']
        trace.reranked = inner['reranked']
        return (Answer(query=query, text=payload['text'],
                       citations=payload['citations'], refused=payload['refused'],
                       reason=payload['reason'], from_cache=bool(hit),
                       sources=payload['sources']), trace)

    def stats(self) -> dict:
        return {'chunks': len(self.chunks), **self.cache.stats()}

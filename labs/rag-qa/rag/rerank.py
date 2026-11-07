"""Second-stage reranking: query-term coverage + MMR diversity.

Vector retrieval optimises global semantic similarity; a short factual query
often needs exact term coverage instead. The reranker combines coverage with
the retrieval score, then applies Maximal Marginal Relevance so the final
context is not three near-duplicate chunks from the same passage.
"""
from __future__ import annotations

import math

from .embeddings import tokenize
from .vectorstore import Hit


def _coverage(query_terms: set[str], text_tokens: list[str]) -> float:
    if not query_terms:
        return 0.0
    present = set(text_tokens)
    return len(query_terms & present) / len(query_terms)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def rerank(query: str, hits: list[Hit], top_n: int = 3,
           coverage_weight: float = 0.45, diversity_weight: float = 0.25,
           vectors: dict[str, list[float]] | None = None) -> list[Hit]:
    """Return at most `top_n` hits reordered by coverage and diversity.

    `coverage_weight` blends lexical coverage into the vector score;
    `diversity_weight` penalises candidates too similar to already selected
    ones (MMR). Set both to 0 for a pure vector ranking.
    """
    if top_n < 1:
        raise ValueError('top_n must be >= 1')
    if not hits:
        return []
    query_terms = set(tokenize(query))
    max_score = max(hit.score for hit in hits) or 1.0
    token_sets = {hit.chunk.chunk_id: set(tokenize(hit.chunk.text)) for hit in hits}

    def base_score(hit: Hit) -> float:
        vector_part = hit.score / max_score
        return ((1 - coverage_weight) * vector_part
                + coverage_weight * _coverage(query_terms, tokenize(hit.chunk.text)))

    selected: list[Hit] = []
    pool = list(hits)
    while pool and len(selected) < top_n:
        best, best_value = None, -math.inf
        for hit in pool:
            redundancy = max(
                (_jaccard(token_sets[hit.chunk.chunk_id],
                          token_sets[chosen.chunk.chunk_id]) for chosen in selected),
                default=0.0)
            value = base_score(hit) - diversity_weight * redundancy
            if value > best_value:
                best, best_value = hit, value
        assert best is not None
        selected.append(best)
        pool.remove(best)
    return selected


def context_budget(hits: list[Hit], max_chars: int = 900) -> list[Hit]:
    """Trim the context so a prompt cannot grow without bound."""
    kept, used = [], 0
    for hit in hits:
        if used + len(hit.chunk.text) > max_chars:
            break
        kept.append(hit)
        used += len(hit.chunk.text)
    return kept

"""Grounded answer generation.

The default generator is extractive: it stitches the highest-scoring chunks
into an answer and refuses when retrieval confidence is too low. This keeps the
lab honest about hallucinations — an answer is only produced when the evidence
passes a threshold, and every sentence carries a citation. An optional
OpenAI-compatible chat backend can be plugged in with the same interface.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .vectorstore import Hit


@dataclass
class Draft:
    text: str
    citations: list[str] = field(default_factory=list)
    refused: bool = False
    reason: str = ''
    sources: list[str] = field(default_factory=list)  # doc_ids actually used as evidence


class Generator(Protocol):
    def generate(self, query: str, hits: list[Hit]) -> Draft: ...


class ExtractiveGenerator:
    """Deterministic, citation-bearing answers with an abstention threshold."""

    def __init__(self, min_score: float = 0.22, min_coverage: float = 0.15,
                 max_hits: int = 3, evidence_ratio: float = 0.85):
        self.min_score = min_score
        self.min_coverage = min_coverage
        self.max_hits = max_hits
        self.evidence_ratio = evidence_ratio

    def _coverage(self, query: str, text: str) -> float:
        from .embeddings import content_terms
        terms = content_terms(query)
        if not terms:
            return 0.0
        return len(terms & content_terms(text)) / len(terms)

    def generate(self, query: str, hits: list[Hit]) -> Draft:
        if not hits:
            return Draft(text='', refused=True, reason='no_candidate')
        best = hits[0]
        coverage = self._coverage(query, best.chunk.text)
        # Abstain when BOTH signals are weak: a low vector score and little
        # exact term overlap. Any unrelated query has ~0 bigram overlap with
        # the corpus, which is what keeps the negative cases honest.
        if best.score < self.min_score or coverage < self.min_coverage:
            return Draft(text='', refused=True, reason='low_confidence')

        # Evidence consistency: only keep candidates whose score is close to
        # the best one. Otherwise a loosely related third chunk (e.g. another
        # scenic spot) gets pasted into the answer and dilutes the citation.
        floor = best.score * self.evidence_ratio
        used = [h for h in hits[:self.max_hits] if h.score >= floor] or [best]
        parts = [h.chunk.text.strip() for h in used]
        text = '\n'.join(parts)
        citations = [h.chunk.citation for h in used]
        sources = list(dict.fromkeys(h.chunk.doc_id for h in used))
        return Draft(text=text, citations=citations, sources=sources)


class ChatGenerator:
    """Optional OpenAI-compatible `/chat/completions` backend.

    Only enabled when MODEL_BASE_URL, a key and MODEL_NAME are configured. The
    prompt is built exclusively from retrieved chunks and the model is
    instructed to abstain when evidence is insufficient.
    """

    def __init__(self, timeout: float = 30.0):
        self.base = os.getenv('MODEL_BASE_URL', '').rstrip('/')
        self.key = os.getenv('MODEL_API_KEY') or os.getenv('GROQ_API_KEY', '')
        self.model = os.getenv('MODEL_NAME', '')
        if not (self.base and self.key and self.model):
            raise RuntimeError('chat endpoint is not configured')
        self.timeout = timeout
        self.extractive = ExtractiveGenerator()

    def generate(self, query: str, hits: list[Hit]) -> Draft:
        guard = self.extractive.generate(query, hits)
        if guard.refused:
            return guard
        context = '\n\n'.join(
            f'[{h.chunk.citation}] {h.chunk.text}' for h in hits[:3])
        prompt = (
            '仅依据下面的资料回答问题，不要使用资料之外的知识。'
            '如果资料不足以回答，只输出「资料不足」。每个结论后标注来源编号。\n\n'
            f'资料：\n{context}\n\n问题：{query}')
        response = httpx.post(
            self.base + '/chat/completions',
            headers={'Authorization': 'Bearer ' + self.key},
            json={'model': self.model,
                  'messages': [{'role': 'user', 'content': prompt}],
                  'temperature': 0, 'stream': False},
            timeout=self.timeout)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content'].strip()
        if not content or '资料不足' in content:
            return Draft(text='', refused=True, reason='model_abstained')
        used = hits[:3]
        return Draft(text=content, citations=[h.chunk.citation for h in used],
                     sources=list(dict.fromkeys(h.chunk.doc_id for h in used)))


def default_generator() -> Generator:
    if all(os.getenv(name) for name in ('MODEL_BASE_URL', 'MODEL_NAME')) and (
            os.getenv('MODEL_API_KEY') or os.getenv('GROQ_API_KEY')):
        try:
            return ChatGenerator()
        except RuntimeError:
            pass
    return ExtractiveGenerator()

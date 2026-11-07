"""Embedding backends: a local hashed TF-IDF vectoriser and an optional
OpenAI-compatible dense endpoint.

The local backend is deliberately simple but real: character bigrams for CJK,
word unigrams for ASCII, sublinear TF, smoothed IDF fitted on the corpus, and
L2 normalisation. It is deterministic and needs no model download. The
`DenseEmbedding` adapter shows the same interface against a real embedding API
without making the lab depend on one.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from typing import Protocol

import httpx

CJK = re.compile(r'[\u3400-\u9fff]')
WORD = re.compile(r'[a-z0-9]+')
DIM = 512


class Embedder(Protocol):
    dimension: int

    def fit(self, texts: list[str]) -> None: ...
    def encode(self, text: str) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    """CJK character unigrams + bigrams, plus lowercase ASCII words."""
    lowered = text.lower()
    tokens: list[str] = []
    runs = re.findall(r'[\u3400-\u9fff]+', lowered)
    for run in runs:
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(WORD.findall(lowered))
    return tokens or ['<empty>']


def content_terms(text: str) -> set[str]:
    """Meaningful terms only: CJK bigrams and multi-character ASCII words.

    Single CJK characters are excluded because common function words ('的',
    '是') create spurious lexical overlap between unrelated queries and chunks.
    """
    return {token for token in tokenize(text) if len(token) > 1}


def _bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big') % DIM


class HashingTfidfEmbedder:
    """Deterministic sparse-to-dense hashed TF-IDF vectors."""

    def __init__(self, dimension: int = DIM):
        self.dimension = dimension
        self.idf: dict[int, float] = {}
        self._fitted = False

    def fit(self, texts: list[str]) -> None:
        df: Counter[int] = Counter()
        for text in texts:
            df.update(set(_bucket(t) for t in tokenize(text)))
        total = max(1, len(texts))
        self.idf = {bucket: math.log((1 + total) / (1 + count)) + 1.0
                    for bucket, count in df.items()}
        self._fitted = True

    def encode(self, text: str) -> list[float]:
        counts = Counter(_bucket(t) for t in tokenize(text))
        vector = [0.0] * self.dimension
        if not self._fitted:
            # Unfitted fallback: plain sublinear TF, still deterministic.
            for bucket, count in counts.items():
                vector[bucket] = 1.0 + math.log(count)
        else:
            for bucket, count in counts.items():
                vector[bucket] = (1.0 + math.log(count)) * self.idf.get(bucket, 1.0)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def state(self) -> dict:
        return {'dimension': self.dimension, 'idf': {str(k): v for k, v in self.idf.items()}}

    def load_state(self, state: dict) -> None:
        self.dimension = int(state['dimension'])
        self.idf = {int(k): float(v) for k, v in state['idf'].items()}
        self._fitted = True


class DenseEmbedding:
    """Optional OpenAI-compatible `/embeddings` backend.

    Enabled only when MODEL_BASE_URL + MODEL_API_KEY + EMBEDDING_MODEL are set.
    """

    def __init__(self, timeout: float = 20.0):
        self.base = os.getenv('MODEL_BASE_URL', '').rstrip('/')
        self.key = os.getenv('MODEL_API_KEY') or os.getenv('GROQ_API_KEY', '')
        self.model = os.getenv('EMBEDDING_MODEL', '')
        if not (self.base and self.key and self.model):
            raise RuntimeError('embedding endpoint is not configured')
        self.timeout = timeout
        self.dimension = 0

    def fit(self, texts: list[str]) -> None:
        return None  # remote models are already trained

    def _request(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            self.base + '/embeddings',
            headers={'Authorization': 'Bearer ' + self.key},
            json={'model': self.model, 'input': texts}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload['data'], key=lambda row: row.get('index', 0))
        vectors = [row['embedding'] for row in data]
        if not vectors or not isinstance(vectors[0], list):
            raise ValueError('provider returned no embedding vectors')
        self.dimension = len(vectors[0])
        return vectors

    def encode(self, text: str) -> list[float]:
        vector = self._request([text])[0]
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def default_embedder() -> Embedder:
    """Prefer the configured dense endpoint, otherwise use the local one."""
    if all(os.getenv(name) for name in ('MODEL_BASE_URL', 'EMBEDDING_MODEL')) and (
            os.getenv('MODEL_API_KEY') or os.getenv('GROQ_API_KEY')):
        try:
            return DenseEmbedding()
        except RuntimeError:
            pass
    return HashingTfidfEmbedder()


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError('embedding dimension mismatch')
    return sum(a * b for a, b in zip(left, right))

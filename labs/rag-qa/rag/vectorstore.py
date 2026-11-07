"""In-memory vector store with brute-force cosine search and an inverted
posting index that skips zero-overlap candidates.

Brute force over a few hundred chunks is exact and fast enough for a lab; the
posting list exists to show the retrieval scaling idea and is verified against
the exhaustive result in the tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .chunking import Chunk
from .embeddings import cosine


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class VectorStore:
    def __init__(self):
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        self._postings: dict[int, set[int]] = {}

    def __len__(self) -> int:
        return len(self.chunks)

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError('chunks/vectors length mismatch')
        for chunk, vector in zip(chunks, vectors):
            index = len(self.chunks)
            self.chunks.append(chunk)
            self.vectors.append(vector)
            for dimension, value in enumerate(vector):
                if value:
                    self._postings.setdefault(dimension, set()).add(index)

    def _candidates(self, query: list[float], use_index: bool) -> list[int]:
        if not use_index:
            return list(range(len(self.chunks)))
        active = [dimension for dimension, value in enumerate(query) if value]
        if not active:
            return []
        found: set[int] = set()
        for dimension in active:
            found.update(self._postings.get(dimension, ()))
        return sorted(found)

    def search(self, query: list[float], top_k: int = 5,
               use_index: bool = True) -> list[Hit]:
        if top_k < 1:
            raise ValueError('top_k must be >= 1')
        scored = []
        for index in self._candidates(query, use_index):
            score = cosine(query, self.vectors[index])
            if score > 0:
                scored.append(Hit(self.chunks[index], score))
        scored.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return scored[:top_k]

    def save(self, path: str | Path) -> None:
        payload = {
            'chunks': [{
                'chunk_id': c.chunk_id, 'doc_id': c.doc_id, 'title': c.title,
                'text': c.text, 'position': c.position, 'source': c.source,
            } for c in self.chunks],
            'vectors': self.vectors,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    def load(self, path: str | Path, embedder_state: dict | None = None) -> None:
        del embedder_state  # vectors are stored, no re-embedding needed
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        self.chunks = [Chunk(**item) for item in payload['chunks']]
        self.vectors = payload['vectors']
        self._postings = {}
        for index, vector in enumerate(self.vectors):
            for dimension, value in enumerate(vector):
                if value:
                    self._postings.setdefault(dimension, set()).add(index)

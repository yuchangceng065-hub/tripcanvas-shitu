"""Document loading and character-window chunking with sentence-boundary snap.

Why character windows instead of a tokenizer: the lab must run without any
third-party dependency, and Chinese has no word boundaries. Windows are
snapped to the nearest sentence terminator so a chunk never ends mid-sentence
when an acceptable boundary is nearby.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

BOUNDARIES = '。！？；\n.!?;'
BOUNDARY_RE = re.compile(r'[。！？；\n.!?;]')


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    text: str
    source: str = ''


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    position: int          # 0-based order inside its source document
    source: str = ''

    @property
    def citation(self) -> str:
        return f'{self.title}#{self.position + 1}'


def load_documents(path: str | Path) -> list[Document]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError('corpus must be a JSON array')
    docs = []
    for item in payload:
        if not isinstance(item, dict) or not all(
                isinstance(item.get(k), str) for k in ('id', 'title', 'text')):
            raise ValueError('each corpus entry needs string id/title/text')
        docs.append(Document(doc_id=item['id'], title=item['title'],
                             text=item['text'], source=item.get('source', '')))
    if not docs:
        raise ValueError('empty corpus')
    return docs


def _snap(text: str, start: int, end: int, size: int, tolerance: float) -> int:
    """Move `end` to a nearby sentence boundary when that costs little."""
    if end >= len(text):
        return end
    floor = max(start + int(size * (1 - tolerance)), start + 1)
    window = text[floor:end + int(size * tolerance)]
    hits = [m.start() for m in BOUNDARY_RE.finditer(window)]
    if not hits:
        return end
    candidate = floor + hits[-1] + 1
    return candidate if candidate > start else end


def _snap_start(text: str, start: int, size: int, tolerance: float) -> int:
    """Back up the overlap start to a sentence beginning.

    Without this, `start = end - overlap` can land in the middle of a word
    ('六月' -> '月'). Snapping backwards never drops text; it only lengthens
    the overlap, and is bounded so the loop always advances.
    """
    if start <= 0:
        return start
    floor = max(0, start - int(size * tolerance))
    window = text[floor:start]
    hits = [m.start() for m in BOUNDARY_RE.finditer(window)]
    return floor + hits[-1] + 1 if hits else start


def split_document(doc: Document, size: int = 180, overlap: int = 40,
                   tolerance: float = 0.25) -> list[Chunk]:
    """Split one document into overlapping chunks.

    Args:
        size: target characters per chunk.
        overlap: characters shared with the previous chunk, so an answer that
            straddles a boundary is still retrievable.
        tolerance: how far (fraction of size) `end` may snap to a boundary.
    """
    if size < 20:
        raise ValueError('chunk size must be >= 20')
    if not 0 <= overlap < size:
        raise ValueError('overlap must satisfy 0 <= overlap < size')
    text = doc.text.strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    start, position = 0, 0
    while start < len(text):
        end = min(start + size, len(text))
        end = _snap(text, start, end, size, tolerance)
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(
                chunk_id=f'{doc.doc_id}:{position}', doc_id=doc.doc_id,
                title=doc.title, text=piece, position=position, source=doc.source))
            position += 1
        if end >= len(text):
            break
        proposed = max(end - overlap, start + 1)
        snapped = _snap_start(text, proposed, size, tolerance)
        start = max(snapped, start + 1)
    return chunks


def chunk_corpus(docs: list[Document], size: int = 180,
                 overlap: int = 40) -> list[Chunk]:
    result: list[Chunk] = []
    for doc in docs:
        result.extend(split_document(doc, size=size, overlap=overlap))
    if not result:
        raise ValueError('no chunks produced')
    return result

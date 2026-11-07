"""Semantic answer cache with three classic failure modes handled explicitly.

* penetration (穿透): a query with no answer is cached briefly with a NULL
  marker, so repeated misses do not hammer the retriever.
* avalanche (雪崩): TTL is jittered so a batch of keys never expires together.
* breakdown (击穿): a per-key single-flight lock lets only one caller
  recompute an expired hot key.

Backends:
* MemoryBackend - pure standard library, LRU + expiry, default.
* RedisBackend  - real Redis commands (SET with PX, GET, DEL, SCAN). Imported
  lazily; when `redis` is not installed the pipeline falls back to memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_ms: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def stats(self) -> dict[str, int]: ...


class MemoryBackend:
    """LRU cache with millisecond TTL; used when Redis is unavailable."""

    def __init__(self, max_entries: int = 256, now=time.monotonic):
        self.max_entries = max_entries
        self._now = now
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        expires_at, _ = entry
        if self._now() >= expires_at:
            del self._data[key]
            return True
        return False

    def get(self, key: str) -> Any | None:
        if self._expired(key):
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return self._data[key][1]

    def set(self, key: str, value: Any, ttl_ms: int) -> None:
        self._data[key] = (self._now() + ttl_ms / 1000.0, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def stats(self) -> dict[str, int]:
        return {'hits': self.hits, 'misses': self.misses,
                'entries': len(self._data), 'backend': 'memory'}


class RedisBackend:
    """Real Redis backend. `client` is injected so it can be faked in tests."""

    def __init__(self, client: Any, namespace: str = 'ragqa'):
        self.client = client
        self.namespace = namespace
        self.hits = 0
        self.misses = 0

    def _key(self, key: str) -> str:
        return f'{self.namespace}:{key}'

    def get(self, key: str) -> Any | None:
        raw = self.client.get(self._key(key))
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        return json.loads(raw)

    def set(self, key: str, value: Any, ttl_ms: int) -> None:
        # SET ... PX is the expiring string structure used for the cache.
        self.client.set(self._key(key), json.dumps(value, ensure_ascii=False), px=ttl_ms)

    def delete(self, key: str) -> None:
        self.client.delete(self._key(key))

    def stats(self) -> dict[str, int]:
        return {'hits': self.hits, 'misses': self.misses,
                'entries': -1, 'backend': 'redis'}


def redis_backend_from_env() -> RedisBackend | None:
    """Build a Redis backend only when configured and importable."""
    url = os.getenv('RAG_REDIS_URL')
    if not url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError:
        return None
    return RedisBackend(redis.Redis.from_url(url, decode_responses=True))


def normalise_query(query: str) -> str:
    """Strip ALL whitespace so '西湖 荷花' and '西湖荷花' share a cache key.

    CJK has no word boundaries, so a stray space must not create a new key;
    for ASCII queries this merely folds 'West Lake' to 'westlake', which is
    fine for cache-key purposes (the retriever still sees the original text).
    """
    return ''.join(query.split()).lower()


def cache_key(query: str, top_k: int, top_n: int) -> str:
    raw = json.dumps({'q': normalise_query(query), 'top_k': top_k, 'top_n': top_n},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


class SemanticCache:
    def __init__(self, backend: CacheBackend | None = None, base_ttl_ms: int = 60_000,
                 null_ttl_ms: int = 5_000, jitter: float = 0.2):
        self.backend = backend or MemoryBackend()
        self.base_ttl_ms = base_ttl_ms
        self.null_ttl_ms = null_ttl_ms
        self.jitter = jitter
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.recomputations = 0

    def _ttl(self) -> int:
        # Jitter breaks synchronised expiry (avalanche protection).
        factor = 1.0 + random.uniform(-self.jitter, self.jitter)
        return max(1, int(self.base_ttl_ms * factor))

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_or_compute(self, key: str, compute, force: bool = False):
        """Single-flight wrapper: only one caller recomputes a missing key."""
        if not force:
            cached = self.backend.get(key)
            if cached is not None:
                if cached.get('__null__'):
                    return None, True
                return cached, True
        lock = self._lock_for(key)
        with lock:
            if not force:
                cached = self.backend.get(key)
                if cached is not None:
                    if cached.get('__null__'):
                        return None, True
                    return cached, True
            self.recomputations += 1
            value = compute()
            if value is None:
                self.backend.set(key, {'__null__': True}, self.null_ttl_ms)
            else:
                self.backend.set(key, value, self._ttl())
            return value, False

    def stats(self) -> dict[str, int]:
        return {**self.backend.stats(), 'recomputations': self.recomputations}

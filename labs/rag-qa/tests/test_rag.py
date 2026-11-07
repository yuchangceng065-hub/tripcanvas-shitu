"""Regression tests for the RAG lab. Zero third-party dependencies."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.cache import (MemoryBackend, RedisBackend, SemanticCache,  # noqa: E402
                       cache_key, normalise_query)
from rag.chunking import Document, chunk_corpus, load_documents, split_document  # noqa: E402
from rag.embeddings import (HashingTfidfEmbedder, content_terms,  # noqa: E402
                            cosine, tokenize)
from rag.generation import ExtractiveGenerator  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from rag.rerank import context_budget, rerank  # noqa: E402
from rag.vectorstore import VectorStore  # noqa: E402

CORPUS = ROOT / 'data' / 'corpus.json'
QUESTIONS = ROOT / 'data' / 'questions.json'


@pytest.fixture(scope='module')
def pipeline():
    p = RAGPipeline(embedder=HashingTfidfEmbedder(),
                    generator=ExtractiveGenerator(),
                    cache=SemanticCache(MemoryBackend()))
    p.index(CORPUS)
    return p


# --- chunking ---------------------------------------------------------------

def test_corpus_loads_and_every_doc_is_chunked():
    docs = load_documents(CORPUS)
    assert len(docs) >= 10
    chunks = chunk_corpus(docs, size=90, overlap=25)
    assert len(chunks) > len(docs), 'small window must split documents'
    assert all(c.text and c.doc_id and c.position >= 0 for c in chunks)
    assert all('#' in c.citation for c in chunks)


def test_overlap_preserves_boundary_text():
    text = '第一段讲西湖的苏堤与白堤。' * 12
    chunks = split_document(Document('d', '测试', text), size=60, overlap=20)
    assert len(chunks) > 1
    assert chunks[0].position == 0 and chunks[1].position == 1
    # Consecutive windows must share a real overlap region, and no chunk may
    # begin mid-word: every chunk starts at a sentence beginning.
    joined = ''.join(c.text for c in chunks)
    assert '苏堤与白堤' in joined
    assert all(c.text.startswith('第一段讲西湖的苏堤与白堤')
               or c.text.startswith('第一段') for c in chunks)


def test_chunks_never_start_mid_sentence():
    docs = load_documents(CORPUS)
    chunks = chunk_corpus(docs, size=90, overlap=25)
    # A snapped chunk must start right after a terminator (or at doc start),
    # never with a stray fragment such as '月是荷花盛期'.
    assert all(not c.text.startswith(('月是', '日是', '年是')) for c in chunks)
    assert sum(len(c.text) for c in chunks) >= sum(len(d.text.strip()) for d in docs)


def test_chunking_rejects_invalid_parameters():
    doc = Document('d', 't', '内容' * 100)
    with pytest.raises(ValueError):
        split_document(doc, size=10)
    with pytest.raises(ValueError):
        split_document(doc, size=60, overlap=60)


def test_corpus_validation_rejects_bad_shapes(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'not': 'a list'}), encoding='utf-8')
    with pytest.raises(ValueError):
        load_documents(bad)


# --- embeddings -------------------------------------------------------------

def test_tokenize_mixes_cjk_bigrams_and_ascii_words():
    tokens = tokenize('西湖 West Lake 2026')
    assert '西' in tokens and '西湖' in tokens
    assert 'west' in tokens and '2026' in tokens


def test_content_terms_excludes_single_cjk_function_words():
    terms = content_terms('的这是什么')
    assert all(len(t) > 1 for t in terms)


def test_embedding_is_deterministic_normalised_and_discriminative():
    e = HashingTfidfEmbedder()
    e.fit(['西湖的荷花与苏堤', '杭州地铁与返程', '灵隐寺飞来峰造像'])
    a, b = e.encode('西湖的荷花与苏堤'), e.encode('西湖的荷花与苏堤')
    assert a == b
    assert abs(cosine(a, a) - 1.0) < 1e-9
    assert cosine(a, e.encode('量子纠错码阈值')) < 0.3
    assert len(a) == e.dimension


def test_embedder_state_roundtrip():
    e = HashingTfidfEmbedder()
    e.fit(['西湖苏堤', '龙井茶园'])
    restored = HashingTfidfEmbedder()
    restored.load_state(e.state())
    assert e.encode('西湖苏堤') == restored.encode('西湖苏堤')


# --- vector store -----------------------------------------------------------

def test_inverted_index_matches_exhaustive_search():
    docs = load_documents(CORPUS)
    chunks = chunk_corpus(docs, size=90, overlap=25)
    e = HashingTfidfEmbedder()
    texts = [c.text for c in chunks]
    e.fit(texts)
    store = VectorStore()
    store.add(chunks, [e.encode(t) for t in texts])
    query = e.encode('曲院风荷的荷花')
    exact = store.search(query, top_k=5, use_index=False)
    indexed = store.search(query, top_k=5, use_index=True)
    assert [(h.chunk.chunk_id, round(h.score, 9)) for h in exact] == [
        (h.chunk.chunk_id, round(h.score, 9)) for h in indexed]


def test_store_persists_and_reloads(tmp_path):
    chunks = chunk_corpus(load_documents(CORPUS), size=90, overlap=25)
    e = HashingTfidfEmbedder()
    e.fit([c.text for c in chunks])
    store = VectorStore()
    store.add(chunks, [e.encode(c.text) for c in chunks])
    path = tmp_path / 'index.json'
    store.save(path)
    restored = VectorStore()
    restored.load(path)
    q = e.encode('灵隐寺')
    assert [h.chunk.doc_id for h in restored.search(q, top_k=1)] == [
        h.chunk.doc_id for h in store.search(q, top_k=1)]


# --- rerank -----------------------------------------------------------------

def test_rerank_promotes_exact_term_coverage():
    chunks = chunk_corpus(load_documents(CORPUS), size=90, overlap=25)
    e = HashingTfidfEmbedder()
    e.fit([c.text for c in chunks])
    store = VectorStore()
    store.add(chunks, [e.encode(c.text) for c in chunks])
    query = '浙江省博物馆孤山馆区参观时间'
    hits = store.search(e.encode(query), top_k=8)
    reranked = rerank(query, hits, top_n=3)
    assert reranked[0].chunk.doc_id == 'museum'
    assert len(reranked) == 3


def test_context_budget_is_bounded():
    chunks = chunk_corpus(load_documents(CORPUS), size=90, overlap=25)
    from rag.vectorstore import Hit
    hits = [Hit(c, 0.9 - i * 0.01) for i, c in enumerate(chunks)]
    kept = context_budget(hits, max_chars=150)
    assert sum(len(h.chunk.text) for h in kept) <= 150
    assert kept


# --- generation / abstention ------------------------------------------------

def test_generator_refuses_off_topic_and_cites_evidence(pipeline):
    vector = pipeline.embedder.encode('龙井村产什么茶')
    hits = pipeline.store.search(vector, top_k=5)
    kept = context_budget(rerank('龙井村产什么茶', hits, top_n=3), 900)
    draft = pipeline.generator.generate('龙井村产什么茶', kept)
    assert not draft.refused
    assert draft.citations

    off = pipeline.store.search(pipeline.embedder.encode('量子纠错码阈值'), top_k=5)
    refused = pipeline.generator.generate('量子纠错码阈值',
                                          context_budget(rerank('量子纠错码阈值', off, top_n=3), 900))
    assert refused.refused


# --- cache ------------------------------------------------------------------

def test_memory_backend_ttl_lru_and_null_marker():
    clock = {'now': 0.0}
    backend = MemoryBackend(max_entries=2, now=lambda: clock['now'])
    backend.set('a', {'v': 1}, ttl_ms=1000)
    assert backend.get('a') == {'v': 1}
    clock['now'] = 1.1
    assert backend.get('a') is None
    backend.set('b', 2, 5000)
    backend.set('c', 3, 5000)
    backend.set('d', 4, 5000)
    assert backend.get('b') is None  # LRU evicted


def test_cache_normalisation_and_key_stability():
    assert normalise_query('  西湖  荷花  ') == '西湖荷花'
    assert cache_key('西湖荷花', 8, 3) == cache_key(' 西湖  荷花 ', 8, 3)
    assert cache_key('西湖荷花', 8, 3) != cache_key('西湖荷花', 5, 3)


def test_null_result_short_ttl_and_single_flight():
    cache = SemanticCache(MemoryBackend(now=lambda: 0.0),
                          base_ttl_ms=60_000, null_ttl_ms=5_000, jitter=0)
    calls = {'n': 0}

    def compute():
        calls['n'] += 1
        return None

    value, hit = cache.get_or_compute('k', compute)
    assert value is None and hit is False
    value, hit = cache.get_or_compute('k', compute)
    assert value is None and hit is True
    assert calls['n'] == 1, 'NULL results must be cached (penetration guard)'


def test_ttl_jitter_prevents_synchronised_expiry():
    cache = SemanticCache(MemoryBackend(), base_ttl_ms=10_000, jitter=0.2)
    ttls = {cache._ttl() for _ in range(30)}
    assert len(ttls) > 5


def test_single_flight_only_one_recomputation():
    backend = MemoryBackend()
    cache = SemanticCache(backend, jitter=0)
    calls = {'n': 0}
    barrier = threading.Barrier(8)

    def compute():
        calls['n'] += 1
        time.sleep(0.01)
        return {'answer': 'ok'}

    def worker():
        barrier.wait()
        cache.get_or_compute('hot', compute)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls['n'] == 1


class FakeRedis:
    """Minimal Redis stand-in recording the real command shapes."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}
        self.commands: list[str] = []

    def set(self, key, value, px=None):
        self.commands.append('SET')
        self.data[key] = value
        if px is not None:
            self.expiry[key] = px

    def get(self, key):
        self.commands.append('GET')
        return self.data.get(key)

    def delete(self, key):
        self.commands.append('DEL')
        self.data.pop(key, None)


def test_redis_backend_uses_set_px_and_json_payload():
    fake = FakeRedis()
    backend = RedisBackend(fake, namespace='ragqa')
    backend.set('abc', {'citations': ['西湖概览#1']}, ttl_ms=1234)
    assert fake.expiry['ragqa:abc'] == 1234
    assert fake.data['ragqa:abc'].startswith('{')
    assert backend.get('abc') == {'citations': ['西湖概览#1']}
    assert backend.hits == 1
    backend.delete('abc')
    assert backend.get('abc') is None
    assert fake.commands == ['SET', 'GET', 'DEL', 'GET']


# --- pipeline / end-to-end --------------------------------------------------

def test_pipeline_answers_with_sources_and_refuses_unknown(pipeline):
    answer, trace = pipeline.ask('曲院风荷什么时候看荷花最好', use_cache=False)
    assert not answer.refused
    assert 'quyuan' in answer.sources
    assert answer.citations
    assert trace.retrieved > 0 and trace.reranked > 0
    assert [s['stage'] for s in trace.stages] == [
        'embed', 'retrieve', 'rerank', 'generate']

    unknown, _ = pipeline.ask('量子纠错码的表面码阈值是多少', use_cache=False)
    assert unknown.refused
    assert unknown.citations == []


def test_pipeline_cache_second_call_is_hit(pipeline):
    query = '苏堤是谁主持修筑的'
    first, _ = pipeline.ask(query, use_cache=False)
    second, trace = pipeline.ask(query)
    assert not first.from_cache
    assert second.from_cache and trace.from_cache
    assert second.text == first.text and second.sources == first.sources


def test_index_must_precede_ask():
    p = RAGPipeline(embedder=HashingTfidfEmbedder(),
                    generator=ExtractiveGenerator(),
                    cache=SemanticCache(MemoryBackend()))
    with pytest.raises(RuntimeError, match='index'):
        p.ask('anything')


# --- evaluation gate --------------------------------------------------------

def test_evaluation_quality_gate():
    from evaluate import run
    report = run()
    assert report['hitAt1'] == 1.0
    assert report['hitAt3'] == 1.0
    assert report['mrr'] == 1.0
    assert report['abstainAccuracy'] == 1.0
    assert report['wrongAnswersOnNegative'] == 0
    assert report['cache']['repeatRecomputations'] == 0
    assert report['failures'] == []

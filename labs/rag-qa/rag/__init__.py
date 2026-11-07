"""A small, dependency-free RAG lab.

The core pipeline (chunk -> embed -> retrieve -> rerank -> generate) runs on
the Python standard library alone. Optional backends (Redis, an
OpenAI-compatible embedding / chat endpoint) are pluggable and off by default.
"""
from .pipeline import RAGPipeline, Answer, Trace

__all__ = ['RAGPipeline', 'Answer', 'Trace']

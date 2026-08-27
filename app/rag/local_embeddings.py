"""
Local, free, open-source embeddings for admin-uploaded documents.

Deliberately separate from app/rag/embeddings.py, which calls the paid
Gemini API for the Day 3 policy docs. Uploaded documents use a small local
model instead - BAAI/bge-small-en-v1.5 via fastembed, ~130MB, ONNX Runtime
based (no PyTorch/CUDA dependency chain, which is a real trap in a plain
Docker container - PyTorch's pip wheel pulls in a large CUDA library chain
even for CPU-only use). No API key, no per-call cost.

Why a separate model/collection rather than reusing Gemini everywhere:
different embedding models produce vectors of different dimensions and
in different vector spaces - Gemini's and this model's vectors are NOT
comparable to each other and cannot share one Chroma collection.

First call downloads the model (~130MB) - needs real internet access and
can take a minute. After that it's cached in the container and instant.
"""

from __future__ import annotations

import os

MODEL_NAME = "BAAI/bge-small-en-v1.5"
CACHE_DIR = os.getenv("FASTEMBED_CACHE_DIR", "/code/fastembed_cache")

_model = None


def _get_model():
    global _model
    if _model is None:
        # Imported lazily so importing this module doesn't force the model
        # download until the first real embedding call.
        from fastembed import TextEmbedding

        os.makedirs(CACHE_DIR, exist_ok=True)
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)
    return _model


def embed_texts_local(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _get_model()
    return [vec.tolist() for vec in model.embed(texts)]


def embed_query_local(text: str) -> list[float]:
    return embed_texts_local([text])[0]

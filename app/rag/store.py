"""
Persistent Chroma collection for policy chunks.

We manage embeddings ourselves (via app.rag.embeddings) rather than letting
Chroma call an embedding function internally. That keeps one code path for
"how do we turn text into a vector" that both ingestion and query-time
retrieval share, and keeps it swappable/testable the same way call_llm() is.
"""

import os

import chromadb

COLLECTION_NAME = "policies"
ADMIN_COLLECTION_NAME = "admin_documents"
CHROMA_PATH = os.getenv("CHROMA_PATH", "/code/chroma_data")


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def get_admin_collection():
    """Separate collection for admin-uploaded docs, embedded with the local
    open-source model (different vector dimensions than Gemini's, so this
    can never share a collection with get_collection())."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name=ADMIN_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection():
    """Wipe and recreate the collection - used when re-ingesting from scratch."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # didn't exist yet
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

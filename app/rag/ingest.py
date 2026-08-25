"""
Loads every policy document, chunks it, embeds each chunk, and stores it
in Chroma with source metadata.

Run:  docker compose exec api python -m app.rag.ingest
"""

from __future__ import annotations

import glob
import os
import sys

from app.rag.chunking import chunk_text
from app.rag.embeddings import embed_texts
from app.rag.store import get_collection, reset_collection

POLICY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "policies")

# Gemini's embed_content accepts a batch, but very large batches are worth
# chunking client-side too - keeps requests small and retry-friendly.
EMBED_BATCH_SIZE = 20


def load_documents() -> list[tuple[str, str]]:
    """Returns [(filename, full_text), ...] for every policy doc."""
    paths = sorted(glob.glob(os.path.join(POLICY_DIR, "*.md")))
    docs = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            docs.append((os.path.basename(path), f.read()))
    return docs


def build_chunk_records(docs: list[tuple[str, str]]) -> list[dict]:
    """Turns documents into flat chunk records with source metadata,
    matching how they'll be stored in Chroma."""
    records = []
    for filename, text in docs:
        chunks = chunk_text(text)
        for idx, chunk in enumerate(chunks):
            records.append(
                {
                    "id": f"{filename}::chunk_{idx}",
                    "text": chunk,
                    "metadata": {
                        "source": filename,
                        "chunk_index": idx,
                        "total_chunks": len(chunks),
                    },
                }
            )
    return records


def run(reset: bool = False) -> int:
    docs = load_documents()
    if not docs:
        print(f"No policy documents found in {POLICY_DIR}")
        return 0

    records = build_chunk_records(docs)
    print(f"Loaded {len(docs)} document(s), {len(records)} chunk(s) total.")

    collection = reset_collection() if reset else get_collection()

    for i in range(0, len(records), EMBED_BATCH_SIZE):
        batch = records[i : i + EMBED_BATCH_SIZE]
        vectors = embed_texts([r["text"] for r in batch])
        collection.upsert(
            ids=[r["id"] for r in batch],
            embeddings=vectors,
            documents=[r["text"] for r in batch],
            metadatas=[r["metadata"] for r in batch],
        )
        print(f"  embedded + stored {i + len(batch)}/{len(records)}")

    print(f"Ingestion complete. Collection now has {collection.count()} chunks.")
    return len(records)


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)

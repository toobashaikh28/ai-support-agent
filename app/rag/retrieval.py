"""
Top-k retrieval across both knowledge bases, with a relevance threshold.

Important caveat: the "policies" collection (Gemini embeddings) and the
"admin_documents" collection (local fastembed model) live in DIFFERENT
vector spaces. Their distance scores are not comparable to each other -
a distance of 0.3 from one model does not mean the same thing as 0.3 from
the other. So we retrieve top-k from each source SEPARATELY, filter each
against its own threshold, then combine the surviving results. We never
sort the two sources against each other by raw distance.

Each source is wrapped in its own try/except: if one knowledge base is
unreachable (embedding API down) or simply empty (nothing ingested yet),
retrieval still returns whatever the other source found, rather than
failing the whole request.
"""

from __future__ import annotations

import os

# Cosine distance in Chroma ranges 0 (identical) to 2 (opposite).
# These thresholds are deliberately permissive defaults - retrieve
# generously and let the grounding prompt's own judgment (does this
# chunk actually answer the question) do the finer filtering. Tune down
# (stricter) if the model starts citing marginally-related chunks;
# tune up (looser) if real questions are getting wrongly refused.
# Every citation returned includes its raw distance, so this is tunable
# by watching real query results rather than guessing blind.
POLICY_DISTANCE_THRESHOLD = float(os.getenv("POLICY_DISTANCE_THRESHOLD", "0.9"))
ADMIN_DISTANCE_THRESHOLD = float(os.getenv("ADMIN_DISTANCE_THRESHOLD", "0.9"))

TOP_K_PER_SOURCE = 3


def _retrieve_policies(query: str, k: int) -> list[dict]:
    from app.rag.embeddings import embed_query
    from app.rag.store import get_collection

    collection = get_collection()
    if collection.count() == 0:
        return []

    query_vector = embed_query(query)
    results = collection.query(query_embeddings=[query_vector], n_results=k)

    if not results["documents"] or not results["documents"][0]:
        return []

    out = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        if dist <= POLICY_DISTANCE_THRESHOLD:
            out.append(
                {
                    "text": doc,
                    "source": meta["source"],
                    "chunk_index": meta["chunk_index"],
                    "distance": round(dist, 4),
                    "collection": "policies",
                }
            )
    return out


def _retrieve_admin_docs(query: str, k: int) -> list[dict]:
    from app.rag.local_embeddings import embed_query_local
    from app.rag.store import get_admin_collection

    collection = get_admin_collection()
    if collection.count() == 0:
        return []

    query_vector = embed_query_local(query)
    results = collection.query(query_embeddings=[query_vector], n_results=k)

    if not results["documents"] or not results["documents"][0]:
        return []

    out = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        if dist <= ADMIN_DISTANCE_THRESHOLD:
            out.append(
                {
                    "text": doc,
                    "source": meta["filename"],
                    "chunk_index": meta["chunk_index"],
                    "distance": round(dist, 4),
                    "collection": "admin_documents",
                }
            )
    return out


def retrieve_context(query: str, k_per_source: int = TOP_K_PER_SOURCE) -> list[dict]:
    """Returns a flat list of relevant chunks from both knowledge bases,
    each above its own source's relevance threshold. Empty list means
    genuinely nothing relevant was found anywhere - the caller (chat
    grounding) is responsible for refusing rather than guessing."""
    results: list[dict] = []

    try:
        results.extend(_retrieve_policies(query, k_per_source))
    except Exception as exc:  # noqa: BLE001 - one source failing shouldn't sink the other
        print(f"[retrieval] policies source failed: {exc}")

    try:
        results.extend(_retrieve_admin_docs(query, k_per_source))
    except Exception as exc:  # noqa: BLE001
        print(f"[retrieval] admin_documents source failed: {exc}")

    return results

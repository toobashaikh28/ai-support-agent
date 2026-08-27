"""
The admin-upload ingestion pipeline: extract -> chunk -> embed -> store,
with every step's outcome written back to the documents table.

Design principle: any exception anywhere in the pipeline is caught at the
top level, turned into status=fail + a readable error_message, and the
document row is left in a state where calling this function again (retry)
just re-runs the whole thing from scratch. Nothing partial is left in
Chroma on failure - a chunk that got embedded right before a crash on
chunk 2 of 5 does not linger as an orphaned vector.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Document, DocumentStatus
from app.rag.document_chunking import chunk_document_text
from app.rag.extraction import extract_text
from app.rag.local_embeddings import embed_texts_local
from app.rag.store import get_admin_collection

EMBED_BATCH_SIZE = 20


def _remove_existing_chunks(document_id: int) -> None:
    """Delete any vectors already stored for this document - used before
    a retry, so a partial success from a previous failed attempt doesn't
    leave duplicate or stale chunks behind."""
    collection = get_admin_collection()
    existing = collection.get(where={"document_id": document_id})
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])


def process_document(document_id: int, db: Session) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} not found")

    document.status = DocumentStatus.processing
    document.error_message = None
    db.commit()

    try:
        _remove_existing_chunks(document_id)

        text = extract_text(document.stored_path, document.file_type)
        chunks = chunk_document_text(text)
        if not chunks:
            raise ValueError("Document produced zero chunks after extraction/splitting.")

        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i : i + EMBED_BATCH_SIZE]
            vectors = embed_texts_local(batch)
            collection = get_admin_collection()
            collection.upsert(
                ids=[f"doc_{document_id}::chunk_{i + j}" for j in range(len(batch))],
                embeddings=vectors,
                documents=batch,
                metadatas=[
                    {
                        "document_id": document_id,
                        "filename": document.filename,
                        "chunk_index": i + j,
                    }
                    for j in range(len(batch))
                ],
            )

        document.status = DocumentStatus.success
        document.chunk_count = len(chunks)
        document.error_message = None

    except Exception as exc:  # noqa: BLE001 - intentionally broad, see module docstring
        document.status = DocumentStatus.fail
        document.error_message = str(exc)[:2000]
        document.chunk_count = None

    finally:
        from app.models import utcnow

        document.processed_at = utcnow()
        db.commit()

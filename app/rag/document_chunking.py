"""
Chunks admin-uploaded documents using LangChain's RecursiveCharacterTextSplitter.

This is separate from app/rag/chunking.py (the hand-written chunker used
for the static policy docs from Day 3). Kept deliberately separate: this
pipeline is for user-uploaded files of varying format and quality, where
LangChain's splitter -- which tries paragraph, then sentence, then word
boundaries in order -- is a better fit than the paragraph-only splitter
used for the curated policy markdown.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Character-based, not token-based - RecursiveCharacterTextSplitter's default.
# ~2000 chars is roughly 400-500 tokens, matching the chunk size used
# elsewhere in this project (Day 3's ~500 token chunks).
CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200


def chunk_document_text(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE_CHARS,
        chunk_overlap=CHUNK_OVERLAP_CHARS,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)
    return [c.strip() for c in chunks if c.strip()]

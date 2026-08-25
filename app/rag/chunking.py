"""
Splits a document into overlapping chunks sized by an approximate token
count.

Why approximate: exact tokenization (tiktoken, etc.) requires downloading a
vocabulary file from a remote CDN on first use, which is a needless fragile
dependency for a step that just needs to keep chunks roughly the same size.
We approximate 1 token ~= 0.75 words, which is close enough for chunking
purposes across most modern tokenizers — exact token counts only matter
once text is actually being fed to a specific model.
"""

from __future__ import annotations

import re

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50

_TOKENS_PER_WORD = 1 / 0.75  # ~1.33 tokens per word


def count_tokens(text: str) -> int:
    words = len(text.split())
    return round(words * _TOKENS_PER_WORD)


def _split_into_paragraphs(text: str) -> list[str]:
    """Split on blank lines, keeping headings attached to the paragraph
    that follows them so a chunk never starts with an orphaned '## Title'."""
    raw_blocks = re.split(r"\n\s*\n", text.strip())
    blocks: list[str] = []
    pending_heading = None
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            pending_heading = block
            continue
        if pending_heading:
            block = f"{pending_heading}\n{block}"
            pending_heading = None
        blocks.append(block)
    if pending_heading:
        blocks.append(pending_heading)
    return blocks


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """
    Greedily packs paragraphs into chunks up to `chunk_size` tokens.
    When a chunk is full, the next chunk starts by re-including the last
    `overlap` tokens' worth of paragraphs, so context isn't lost at the
    boundary (e.g. a rule split from the exception right after it).
    """
    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush():
        if current:
            chunks.append("\n\n".join(current))

    def build_overlap() -> tuple[list[str], int]:
        """Take the trailing ~`overlap` tokens of raw text from the chunk
        that was just flushed, regardless of paragraph boundaries. Whole-
        paragraph overlap fails whenever a paragraph is itself bigger than
        the overlap budget (common here, since these paragraphs run
        60-100+ tokens) — a word-level trailing window always works."""
        joined = "\n\n".join(current)
        words = joined.split()
        overlap_word_count = max(1, round(overlap * 0.75))  # tokens -> approx words
        if len(words) <= overlap_word_count:
            return ([joined], count_tokens(joined))
        snippet = " ".join(words[-overlap_word_count:])
        return ([snippet], count_tokens(snippet))

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # a single paragraph longer than chunk_size: emit it alone
        if para_tokens > chunk_size:
            flush()
            chunks.append(para)
            current, current_tokens = [], 0
            continue

        if current_tokens + para_tokens > chunk_size and current:
            flush()
            current, current_tokens = build_overlap()

        current.append(para)
        current_tokens += para_tokens

    flush()
    return chunks

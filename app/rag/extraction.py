"""
Turns an uploaded file into plain text, regardless of format.

Each extractor is deliberately narrow and raises on failure rather than
returning empty/partial text - the ingestion pipeline treats any exception
here as a document-level failure (status=fail), which is exactly what
should happen for a corrupted or unreadable file.
"""

from __future__ import annotations

import os

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return ext.lstrip(".")


def extract_text(file_path: str, file_type: str) -> str:
    if file_type == "pdf":
        return _extract_pdf(file_path)
    if file_type == "docx":
        return _extract_docx(file_path)
    if file_type in ("txt", "md"):
        return _extract_plain(file_path)
    raise ValueError(f"No extractor for file_type '{file_type}'")


def _extract_pdf(file_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages).strip()
    if not text:
        raise ValueError(
            "No extractable text found in PDF (it may be scanned/image-only, "
            "which needs OCR - not supported yet)."
        )
    return text


def _extract_docx(file_path: str) -> str:
    import docx

    doc = docx.Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n\n".join(paragraphs).strip()
    if not text:
        raise ValueError("No extractable text found in the Word document.")
    return text


def _extract_plain(file_path: str) -> str:
    with open(file_path, encoding="utf-8", errors="replace") as f:
        text = f.read().strip()
    if not text:
        raise ValueError("File is empty.")
    return text

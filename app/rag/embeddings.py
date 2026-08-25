"""
Thin wrapper around the Gemini embedding API. Kept separate from llm.py
(which handles chat generation) because embeddings and chat are different
concerns that happen to share a provider — swapping one shouldn't risk
breaking the other.
"""

import os

from google import genai
from google.genai import types

EMBEDDING_MODEL = "gemini-embedding-001"

_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)
    return _client


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """
    task_type differs for what's being embedded:
      - RETRIEVAL_DOCUMENT: policy chunks going INTO the vector store
      - RETRIEVAL_QUERY: a user's question going in to search the store
    Using the right one measurably improves retrieval quality - the model
    embeds a question and an answer passage slightly differently.
    """
    if not texts:
        return []
    client = get_client()
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return [e.values for e in response.embeddings]


def embed_query(text: str) -> list[float]:
    return embed_texts([text], task_type="RETRIEVAL_QUERY")[0]

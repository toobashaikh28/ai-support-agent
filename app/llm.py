"""
Thin wrapper around the Gemini API.

Kept in its own module and behind a single function, call_llm(), so the
rest of the app never touches the genai client directly. That means:
  - swapping models/providers later only touches this file
  - tests can monkeypatch call_llm() instead of mocking a network call
"""

import os

from google import genai
from google.genai import types

MODEL = "gemini-3.6-flash"

_client: genai.Client | None = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to your .env file "
                "(never commit the real key)."
            )
        _client = genai.Client(api_key=api_key)
    return _client


SYSTEM_PROMPT = (
    "You are a helpful customer support assistant. Be concise and friendly. "
    "You do not yet have access to real order data or tools — that is added "
    "in a later stage of this project. If asked about a specific order, "
    "payment, or account detail, say you don't have access to that yet "
    "rather than inventing an answer."
)


def call_llm(history: list[dict[str, str]]) -> str:
    """
    history: list of {"role": "user"|"assistant", "content": str}, oldest first.
    Returns the assistant's reply text.
    """
    client = get_client()

    # Gemini uses "model" instead of "assistant", and wraps text in Part objects.
    contents = [
        types.Content(
            role="model" if turn["role"] == "assistant" else "user",
            parts=[types.Part(text=turn["content"])],
        )
        for turn in history
    ]

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return response.text

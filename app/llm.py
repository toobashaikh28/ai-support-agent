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


TRANSCRIBE_PROMPT = (
    "Transcribe the following audio exactly, word for word, in the language "
    "it was spoken in. Return ONLY the transcript text — no preamble, no "
    "quotation marks, no commentary. If the audio is silent or unintelligible, "
    "return an empty string."
)


def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """
    Sends raw audio bytes straight to Gemini (which is multimodal) and asks
    for a plain transcript back. Kept in this module, next to call_llm, so
    both LLM-touching operations live in one place and are both easy to
    monkeypatch in tests.

    mime_type should match what the browser recorded, e.g. "audio/webm" or
    "audio/wav" - Gemini accepts common audio containers directly.
    """
    client = get_client()

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    types.Part(text=TRANSCRIBE_PROMPT),
                ],
            )
        ],
    )
    return (response.text or "").strip()


def call_llm(history: list[dict[str, str]], system_prompt: str | None = None) -> str:
    """
    history: list of {"role": "user"|"assistant", "content": str}, oldest first.
    system_prompt: overrides the default persona - used by the RAG grounding
    layer (app/rag/chat_grounding.py) to inject retrieved context and strict
    "answer only from this" instructions per-call.
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
        config=types.GenerateContentConfig(system_instruction=system_prompt or SYSTEM_PROMPT),
    )
    return response.text

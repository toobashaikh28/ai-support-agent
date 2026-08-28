"""
Thin wrapper around the Gemini API.

Kept in its own module and behind a single function, call_llm(), so the
rest of the app never touches the genai client directly. That means:
  - swapping models/providers later only touches this file
  - tests can monkeypatch call_llm() instead of mocking a network call
"""

import io
import os
import wave

from google import genai
from google.genai import types

MODEL = "gemini-3.6-flash"

# Gemini's dedicated speech-generation model - a genuinely natural, expressive
# voice, not the robotic default that ships with browsers. Kept as a separate
# constant from MODEL since it's a different model family (audio-out only,
# text-in only) with its own preview limitations.
TTS_MODEL = "gemini-3.1-flash-tts-preview"
# One of Gemini's 30 prebuilt voices - warm and neutral, a reasonable
# default for a support agent. Swap this to try others; the full gallery
# is browsable live in Google AI Studio's Voice Library before committing.
TTS_VOICE = "Kore"

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


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """
    Gemini's TTS model returns raw 16-bit mono PCM, not a playable audio
    file - there's no header describing sample rate/channels/bit depth, so
    a browser <audio> element can't play it as-is. Wrapping it in a
    standard WAV header (via the stdlib wave module, no ffmpeg needed)
    turns it into a normal .wav file any browser can play directly.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)       # mono
        wav_file.setsampwidth(2)       # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def synthesize_speech(text: str, voice: str = TTS_VOICE) -> bytes:
    """
    Turns reply text into natural-sounding speech and returns playable WAV
    bytes. Used wherever the app previously relied on the browser's
    built-in (robotic) speechSynthesis - the live call and the "replay
    this reply" button.

    Kept as a single call per full reply rather than streamed - simpler,
    and reply lengths in this app are short enough that the latency is
    acceptable. If replies grow much longer, this is the first place to
    revisit (split into sentence-level chunks and stream them).
    """
    client = get_client()

    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=text)])],
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )

    pcm_bytes = response.candidates[0].content.parts[0].inline_data.data
    return _pcm_to_wav(pcm_bytes)

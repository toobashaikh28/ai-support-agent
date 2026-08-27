"""
Per-message sentiment + complaint-type classification.

Runs once, right after a customer message is stored, so two later steps
can both react to it:
  - app/rag/chat_grounding.py switches to an empathetic, solution-first
    tone when the message describes an actual problem (a broken parcel,
    not just a policy question).
  - app/rag/escalation.py decides whether the conversation needs a human,
    using this classification as one of its inputs.

Kept as its own module (rather than folded into chat_grounding) because
it's a distinct concern - classification is judged right/wrong against a
labelled test set, generation is judged on the response itself.
"""

from __future__ import annotations

import json

from google.genai import types

from app.llm import MODEL, get_client

SENTIMENT_LEVELS = ["positive", "neutral", "frustrated", "angry", "very_angry"]

# "general_question" = no complaint, just asking something ("what's your
# return window?"). Everything else describes an actual problem the
# customer is experiencing right now.
COMPLAINT_TYPES = [
    "damaged_item",
    "wrong_item",
    "missing_item",
    "late_delivery",
    "billing_issue",
    "account_issue",
    "general_question",
    "other",
]

DEFAULT_RESULT = {"sentiment": "neutral", "complaint_type": "general_question"}

CLASSIFY_PROMPT_TEMPLATE = (
    "Classify the customer support message below on two axes and return "
    "ONLY a JSON object - no markdown, no other text.\n\n"
    "sentiment: exactly one of {sentiment_levels}\n"
    "complaint_type: exactly one of {complaint_types} - use "
    "\"general_question\" for a plain question with no complaint, "
    "\"other\" for a real complaint that doesn't fit the listed categories.\n\n"
    "Respond in this exact shape:\n"
    '{{"sentiment": "...", "complaint_type": "..."}}\n\n'
    "Message: {message}"
)


def classify_message(message: str) -> dict:
    """
    Returns {"sentiment": ..., "complaint_type": ...}.

    Falls back to neutral/general_question on any API or parsing failure
    rather than raising - a classification miss should never take down
    the chat endpoint, it should just mean the tone-switch and escalation
    logic quietly don't fire for that one message.
    """
    if not message.strip():
        return dict(DEFAULT_RESULT)

    prompt = CLASSIFY_PROMPT_TEMPLATE.format(
        sentiment_levels=SENTIMENT_LEVELS,
        complaint_types=COMPLAINT_TYPES,
        message=message,
    )

    try:
        client = get_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)

        sentiment = data.get("sentiment")
        complaint_type = data.get("complaint_type")
        if sentiment not in SENTIMENT_LEVELS:
            sentiment = "neutral"
        if complaint_type not in COMPLAINT_TYPES:
            complaint_type = "general_question"
        return {"sentiment": sentiment, "complaint_type": complaint_type}

    except Exception as exc:  # noqa: BLE001 - classification must never break chat
        print(f"[sentiment] classification failed, defaulting to neutral: {exc}")
        return dict(DEFAULT_RESULT)

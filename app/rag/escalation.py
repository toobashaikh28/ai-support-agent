"""
Decides whether a conversation needs a human, and writes the structured
handoff note a support agent reads when they pick up the resulting ticket.

Deliberately split from sentiment.py: sentiment.py asks the model "how does
this message read"; this module is a small set of plain, readable rules on
top of that answer. Anything that decides whether a human gets paged should
be the boring, auditable part of the pipeline, not another model call whose
reasoning you'd have to reverse-engineer later.
"""

from __future__ import annotations

from app.llm import call_llm

HUMAN_REQUEST_PHRASES = (
    "talk to a human",
    "speak to a human",
    "real person",
    "human agent",
    "talk to someone",
    "speak to someone",
    "customer service rep",
    "talk to a person",
    "human representative",
    "escalate this",
    "speak to a manager",
)

# Complaint types that describe an actual unresolved problem, as opposed
# to "general_question" (just asking something, nothing to escalate for).
PROBLEM_COMPLAINT_TYPES = {
    "damaged_item",
    "wrong_item",
    "missing_item",
    "late_delivery",
    "billing_issue",
    "account_issue",
    "other",
}

PRIORITY_BY_REASON = {
    "very_angry_sentiment": "urgent",
    "customer_requested_human": "high",
    "angry_sentiment": "high",
    "frustrated_with_unresolved_issue": "normal",
}

REASON_LABELS = {
    "very_angry_sentiment": "Customer is very upset",
    "customer_requested_human": "Customer asked for a human agent",
    "angry_sentiment": "Customer is angry",
    "frustrated_with_unresolved_issue": "Frustrated customer with an unresolved issue",
}


def wants_human(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in HUMAN_REQUEST_PHRASES)


def should_escalate(message: str, sentiment: str, complaint_type: str) -> tuple[bool, str]:
    """
    Returns (should_escalate, reason). reason is a short internal label
    (see REASON_LABELS / PRIORITY_BY_REASON) - never shown to the customer
    verbatim, just used to set the ticket's priority and category.

    Order matters: an explicit request for a human always wins, even if
    the classifier read the message as calm - a customer can ask for a
    person in a perfectly even tone.
    """
    if wants_human(message):
        return True, "customer_requested_human"
    if sentiment == "very_angry":
        return True, "very_angry_sentiment"
    if sentiment == "angry":
        return True, "angry_sentiment"
    if sentiment == "frustrated" and complaint_type in PROBLEM_COMPLAINT_TYPES:
        return True, "frustrated_with_unresolved_issue"
    return False, ""


SUMMARY_SYSTEM_PROMPT = (
    "You write short, factual handoff notes for human support agents who "
    "are about to take over a conversation from an AI assistant. Never "
    "invent a detail that isn't in the conversation given to you. If "
    "something isn't mentioned (e.g. an order number), say so plainly "
    "rather than guessing."
)

SUMMARY_PROMPT_TEMPLATE = """Write a handoff note for a human support agent, using exactly this format (plain text, no markdown headers, no bullet points):

Customer: {customer_name} ({customer_email})
Issue: <one sentence describing what the customer's actual problem is>
Sentiment: {sentiment}
What the AI already told the customer: <one sentence summarising the AI's most recent reply>
Recommended next step: <one concrete, specific action the human agent should take next>

Conversation so far (oldest first):
{transcript}
"""


def generate_escalation_summary(
    customer_name: str,
    customer_email: str,
    sentiment: str,
    transcript: str,
) -> str:
    """
    transcript: a plain-text rendering of the conversation so far, oldest
    first, e.g. "Customer: ...\\nAgent: ...\\nCustomer: ...".
    """
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        customer_email=customer_email,
        sentiment=sentiment,
        transcript=transcript,
    )
    return call_llm(
        [{"role": "user", "content": prompt}],
        system_prompt=SUMMARY_SYSTEM_PROMPT,
    )

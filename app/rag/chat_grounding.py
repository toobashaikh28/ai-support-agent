"""
Wires retrieval into chat generation: builds a grounding prompt that
constrains the model to answer only from retrieved context, and returns
citations alongside the reply.

Refusal is handled at the prompt level, not by hard-coding a bypass when
retrieval is empty. Bypassing the LLM entirely on empty retrieval would
also break normal conversation ("hi", "thanks") since those never match
any policy chunk. Instead the system prompt is explicit: answer
substantive/factual questions ONLY from the context given, and say so
plainly when nothing relevant was retrieved, but engage normally with
greetings and small talk. This is the standard grounding pattern and is
easy to verify by testing what got retrieved vs. what the model said.
"""

from __future__ import annotations

from app.llm import call_llm
from app.rag.escalation import PROBLEM_COMPLAINT_TYPES
from app.rag.retrieval import retrieve_context

BASE_PERSONA = (
    "You are a helpful customer support assistant for an e-commerce company."
)

GROUNDING_RULES = (
    "You have been given a set of CONTEXT passages retrieved from the company's "
    "policy documents and knowledge base. Follow these rules strictly:\n\n"
    "1. For any factual claim about company policy - returns, refunds, shipping, "
    "warranty, or anything similarly specific - you may ONLY use information "
    "that appears in the CONTEXT below. Never fill in a policy detail from "
    "general knowledge or assumption, even if it sounds plausible.\n"
    "2. If the CONTEXT does not contain the answer to the customer's question, "
    "say plainly that you don't have that information, and suggest they contact "
    "support for specifics. Do not guess or improvise a policy detail.\n"
    "3. You may still respond normally to greetings, thanks, and general "
    "conversational messages that aren't asking for a specific policy fact - "
    "those don't need CONTEXT support.\n"
    "4. When you do use the CONTEXT to answer, be concise and natural - don't "
    "quote the passages verbatim or mention 'the context' to the customer."
)

# Only used when the message has been classified as an actual reported
# problem (a broken parcel, a wrong item, a late delivery) rather than a
# plain informational question - see app/rag/sentiment.py. Deliberately
# separate from GROUNDING_RULES: the grounding constraint always applies,
# this block only changes the *tone and shape* of the reply on top of it.
EMPATHY_RULES_TEMPLATE = (
    "\n\nThis message has been classified as a REPORTED PROBLEM "
    "(complaint_type: {complaint_type}), not just an informational "
    "question. For this reply specifically:\n"
    "1. Open with one brief, genuine line acknowledging the problem - a "
    "short apology for the inconvenience. Don't overdo it or repeat it.\n"
    "2. Then give a concrete next step, grounded in the CONTEXT below - "
    "e.g. how to request a replacement, refund, or return, and any "
    "relevant timeframe or condition. An apology with no next step is not "
    "an acceptable reply for a reported problem.\n"
    "3. If the CONTEXT doesn't cover this specific situation, say so "
    "plainly and let them know a human agent will follow up shortly - "
    "never invent a policy detail to fill the gap."
)


def _build_system_prompt(chunks: list[dict], complaint_type: str | None) -> str:
    if not chunks:
        context_block = "(No relevant context was retrieved for this message.)"
    else:
        parts = []
        for i, c in enumerate(chunks, start=1):
            parts.append(f"[{i}] Source: {c['source']} (chunk {c['chunk_index']})\n{c['text']}")
        context_block = "\n\n".join(parts)

    prompt = f"{BASE_PERSONA}\n\n{GROUNDING_RULES}"

    if complaint_type in PROBLEM_COMPLAINT_TYPES:
        prompt += EMPATHY_RULES_TEMPLATE.format(complaint_type=complaint_type)

    prompt += f"\n\n--- CONTEXT ---\n{context_block}\n--- END CONTEXT ---"
    return prompt


def generate_grounded_reply(
    history: list[dict[str, str]],
    latest_message: str,
    complaint_type: str | None = None,
) -> tuple[str, list[dict]]:
    """
    history: prior conversation turns (see app.llm.call_llm), already
    includes latest_message as the final user turn.
    latest_message: the current user message, used to drive retrieval
    (retrieval runs on the current question, not the whole history).
    complaint_type: from app.rag.sentiment.classify_message - when it's one
    of PROBLEM_COMPLAINT_TYPES, the reply opens with empathy and leads
    with a concrete fix rather than answering as a neutral FAQ lookup.

    Returns (reply_text, citations). citations is empty when nothing
    relevant was retrieved for this message.
    """
    chunks = retrieve_context(latest_message)
    system_prompt = _build_system_prompt(chunks, complaint_type)

    reply = call_llm(history, system_prompt=system_prompt)

    citations = [
        {
            "source": c["source"],
            "chunk_index": c["chunk_index"],
            "collection": c["collection"],
            "distance": c["distance"],
        }
        for c in chunks
    ]
    return reply, citations

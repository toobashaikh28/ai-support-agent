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


def _build_system_prompt(chunks: list[dict]) -> str:
    if not chunks:
        context_block = "(No relevant context was retrieved for this message.)"
    else:
        parts = []
        for i, c in enumerate(chunks, start=1):
            parts.append(f"[{i}] Source: {c['source']} (chunk {c['chunk_index']})\n{c['text']}")
        context_block = "\n\n".join(parts)

    return f"{BASE_PERSONA}\n\n{GROUNDING_RULES}\n\n--- CONTEXT ---\n{context_block}\n--- END CONTEXT ---"


def generate_grounded_reply(
    history: list[dict[str, str]], latest_message: str
) -> tuple[str, list[dict]]:
    """
    history: prior conversation turns (see app.llm.call_llm), already
    includes latest_message as the final user turn.
    latest_message: the current user message, used to drive retrieval
    (retrieval runs on the current question, not the whole history).

    Returns (reply_text, citations). citations is empty when nothing
    relevant was retrieved for this message.
    """
    chunks = retrieve_context(latest_message)
    system_prompt = _build_system_prompt(chunks)

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

from flask import Flask, g, jsonify, request
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database import Base, SessionLocal, engine
from app.llm import call_llm
from app.models import (
    Conversation,
    Customer,
    Message,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    SenderType,
    Ticket,
)
from app.rag.embeddings import embed_query
from app.rag.store import get_collection

app = Flask(__name__)

# Equivalent of FastAPI's @app.on_event("startup") - runs once at import
# time, before the app starts serving requests.
with app.app_context():
    Base.metadata.create_all(engine)


# --------------------------------------------------------------------------
# Per-request DB session, Flask's equivalent of FastAPI's Depends(get_db).
# `g` is a per-request namespace; teardown runs after every request/error.
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = SessionLocal()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        if exception is not None:
            db.rollback()
        db.close()


def api_error(message: str, status: int):
    return jsonify({"detail": message}), status


# --------------------------------------------------------------------------

@app.get("/health")
def health():
    db = get_db()
    db.execute(select(1))
    return jsonify({"status": "ok", "database": "connected"})


CONTEXT_WINDOW = 10  # how many prior messages to feed back to the LLM


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    # Temporary: real auth (Day 11) will derive this from a token instead.
    customer_id: int | None = None


@app.post("/chat")
def chat():
    db = get_db()

    try:
        payload = ChatRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)

    if not payload.message.strip():
        return api_error("message cannot be empty", 400)

    # --- resolve the session (conversation) -----------------------------
    if payload.session_id is not None:
        conversation = db.get(Conversation, payload.session_id)
        if not conversation:
            return api_error("session_id not found", 404)
    else:
        if payload.customer_id is None:
            return api_error("customer_id is required when starting a new session", 400)
        customer = db.get(Customer, payload.customer_id)
        if not customer:
            return api_error("customer_id not found", 404)
        conversation = Conversation(customer_id=customer.id)
        db.add(conversation)
        db.flush()  # assigns conversation.id without committing yet

    # --- store the incoming user message --------------------------------
    db.add(
        Message(
            conversation_id=conversation.id,
            sender_type=SenderType.customer,
            body=payload.message,
        )
    )
    db.flush()

    # --- load recent history for context ---------------------------------
    recent = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.sent_at.desc(), Message.id.desc())
        .limit(CONTEXT_WINDOW)
    ).all()
    recent = list(reversed(recent))  # oldest first for the LLM

    llm_history = [
        {
            "role": "assistant" if m.sender_type == SenderType.agent else "user",
            "content": m.body,
        }
        for m in recent
    ]

    reply_text = call_llm(llm_history)

    # --- store the assistant reply ---------------------------------------
    db.add(
        Message(
            conversation_id=conversation.id,
            sender_type=SenderType.agent,
            sender_user_id=None,  # NULL = the bot, not a human agent
            body=reply_text,
        )
    )
    db.commit()

    return jsonify({"session_id": conversation.id, "reply": reply_text})


@app.get("/chat/<int:session_id>/history")
def chat_history(session_id: int):
    db = get_db()
    conversation = db.get(Conversation, session_id)
    if not conversation:
        return api_error("session_id not found", 404)

    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == session_id)
        .order_by(Message.sent_at, Message.id)
    ).all()

    return jsonify(
        {
            "session_id": session_id,
            "customer_id": conversation.customer_id,
            "messages": [
                {"sender": m.sender_type.value, "body": m.body, "sent_at": m.sent_at.isoformat()}
                for m in messages
            ],
        }
    )


@app.get("/rag/search")
def rag_search():
    q = request.args.get("q", "")
    n = request.args.get("n", 3, type=int)

    if not q.strip():
        return api_error("q cannot be empty", 400)

    collection = get_collection()
    if collection.count() == 0:
        return api_error("No policy chunks ingested yet. Run: python -m app.rag.ingest", 400)

    query_vector = embed_query(q)
    results = collection.query(query_embeddings=[query_vector], n_results=n)

    return jsonify(
        {
            "query": q,
            "results": [
                {
                    "text": doc,
                    "source": meta["source"],
                    "chunk_index": meta["chunk_index"],
                    "distance": dist,
                }
                for doc, meta, dist in zip(
                    results["documents"][0], results["metadatas"][0], results["distances"][0]
                )
            ],
        }
    )


@app.get("/customers/<int:customer_id>/orders")
def customer_orders(customer_id: int):
    """Proves the join chain: customer -> orders -> order_items -> products,
    plus payments per order. This is the query the Day 1 'done when' checks."""
    db = get_db()
    customer = db.get(Customer, customer_id)
    if not customer:
        return api_error("Customer not found", 404)

    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .options(
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.payments),
        )
        .order_by(Order.placed_at)
    ).all()

    return jsonify(
        {
            "customer": {"id": customer.id, "name": customer.full_name, "email": customer.email},
            "orders": [
                {
                    "order_number": o.order_number,
                    "status": o.status.value,
                    "total_amount": str(o.total_amount),
                    "items": [
                        {
                            "product": item.product.name,
                            "quantity": item.quantity,
                            "unit_price": str(item.unit_price),
                            "line_total": str(item.line_total),
                        }
                        for item in o.items
                    ],
                    "payments": [
                        {
                            "amount": str(p.amount),
                            "refunded_amount": str(p.refunded_amount),
                            "status": p.status.value,
                        }
                        for p in o.payments
                    ],
                }
                for o in orders
            ],
        }
    )


@app.get("/stats")
def stats():
    db = get_db()
    return jsonify(
        {
            "customers": db.scalar(select(func.count()).select_from(Customer)),
            "products": db.scalar(select(func.count()).select_from(Product)),
            "orders": db.scalar(select(func.count()).select_from(Order)),
            "tickets": db.scalar(select(func.count()).select_from(Ticket)),
            "orders_by_status": {
                status.value: db.scalar(
                    select(func.count()).select_from(Order).where(Order.status == status)
                )
                for status in OrderStatus
            },
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

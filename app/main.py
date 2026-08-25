from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import Base, engine, get_db
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

app = FastAPI(title="Support API", version="0.1.0")


@app.on_event("startup")
def on_startup() -> None:
    # Fine for day 1. Swap for Alembic migrations before this schema changes.
    Base.metadata.create_all(engine)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "database": "connected"}


CONTEXT_WINDOW = 10  # how many prior messages to feed back to the LLM


class ChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    # Temporary: real auth (Day 11) will derive this from a token instead.
    customer_id: int | None = None


class ChatResponse(BaseModel):
    session_id: int
    reply: str


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    # --- resolve the session (conversation) -----------------------------
    if payload.session_id is not None:
        conversation = db.get(Conversation, payload.session_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="session_id not found")
    else:
        if payload.customer_id is None:
            raise HTTPException(
                status_code=400,
                detail="customer_id is required when starting a new session",
            )
        customer = db.get(Customer, payload.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="customer_id not found")
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

    return ChatResponse(session_id=conversation.id, reply=reply_text)


@app.get("/chat/{session_id}/history")
def chat_history(session_id: int, db: Session = Depends(get_db)):
    conversation = db.get(Conversation, session_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="session_id not found")

    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == session_id)
        .order_by(Message.sent_at, Message.id)
    ).all()

    return {
        "session_id": session_id,
        "customer_id": conversation.customer_id,
        "messages": [
            {"sender": m.sender_type.value, "body": m.body, "sent_at": m.sent_at.isoformat()}
            for m in messages
        ],
    }


@app.get("/customers/{customer_id}/orders")
def customer_orders(customer_id: int, db: Session = Depends(get_db)):
    """Proves the join chain: customer -> orders -> order_items -> products,
    plus payments per order. This is the query the Day 1 'done when' checks."""
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .options(
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.payments),
        )
        .order_by(Order.placed_at)
    ).all()

    return {
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


@app.get("/stats")
def stats(db: Session = Depends(get_db)):
    """Sanity check that the seed landed correctly."""
    return {
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
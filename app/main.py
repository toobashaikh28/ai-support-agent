import os
import secrets
import threading
import uuid

from flask import Flask, g, jsonify, request, send_from_directory
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.auth import hash_password, issue_token, require_admin, require_customer, verify_password
from app.database import Base, SessionLocal, engine
from app.llm import call_llm, transcribe_audio
from app.rag.chat_grounding import generate_grounded_reply
from app.rag.escalation import (
    PRIORITY_BY_REASON,
    REASON_LABELS,
    generate_escalation_summary,
    should_escalate,
)
from app.rag.sentiment import classify_message
from app.models import (
    Conversation,
    Customer,
    Document,
    DocumentStatus,
    FeedbackValue,
    Message,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    SenderType,
    Ticket,
    TicketPriority,
    TicketStatus,
    User,
)
from app.rag.document_pipeline import process_document
from app.rag.embeddings import embed_query
from app.rag.extraction import detect_file_type
from app.rag.store import get_admin_collection, get_collection

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/code/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)

# Equivalent of FastAPI's @app.on_event("startup") - runs once at import
# time, before the app starts serving requests.
with app.app_context():
    Base.metadata.create_all(engine)


@app.get("/admin")
def admin_entry():
    from flask import redirect

    return redirect("/static/admin/login.html")


@app.get("/app")
def user_app_entry():
    from flask import redirect

    return redirect("/static/user/login.html")


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


def _existing_open_ticket(conversation_id: int, db) -> "Ticket | None":
    """A conversation should only ever have one active escalation ticket -
    if the customer's second frustrated message arrives before a human has
    picked up the first ticket, we don't want a fresh duplicate every turn."""
    return db.scalar(
        select(Ticket)
        .where(Ticket.conversation_id == conversation_id)
        .where(Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress, TicketStatus.escalated]))
        .order_by(Ticket.created_at.desc())
        .limit(1)
    )


def _build_transcript(recent_messages: list["Message"], latest_user_message: str, latest_reply: str) -> str:
    lines = []
    for m in recent_messages:
        speaker = "Customer" if m.sender_type == SenderType.customer else "Agent"
        lines.append(f"{speaker}: {m.body}")
    lines.append(f"Customer: {latest_user_message}")
    lines.append(f"Agent: {latest_reply}")
    return "\n".join(lines)


def _maybe_escalate(
    conversation: "Conversation",
    user_message: str,
    reply_text: str,
    sentiment: str,
    complaint_type: str,
    recent_messages: list["Message"],
    db,
) -> dict | None:
    """Runs the escalation rules and, if warranted and not already done for
    this conversation, writes a Ticket with an auto-generated handoff
    summary. Returns None when no escalation happened, otherwise a small
    dict the API responses can surface to the frontend."""
    escalate, reason = should_escalate(user_message, sentiment, complaint_type)
    if not escalate:
        return None

    existing = _existing_open_ticket(conversation.id, db)
    if existing:
        return {
            "ticket_reference": existing.reference,
            "priority": existing.priority.value,
            "already_escalated": True,
        }

    customer = conversation.customer  # lazy-loads within this session
    transcript = _build_transcript(recent_messages, user_message, reply_text)

    try:
        summary = generate_escalation_summary(
            customer_name=customer.full_name,
            customer_email=customer.email,
            sentiment=sentiment,
            transcript=transcript,
        )
    except Exception as exc:  # noqa: BLE001 - a failed summary must not block escalation
        print(f"[escalation] summary generation failed, using a fallback note: {exc}")
        summary = (
            f"Customer: {customer.full_name} ({customer.email})\n"
            f"Issue: (auto-summary unavailable) {user_message}\n"
            f"Sentiment: {sentiment}\n"
            f"What the AI already told the customer: {reply_text}\n"
            f"Recommended next step: Review the conversation and follow up directly."
        )

    priority_value = PRIORITY_BY_REASON.get(reason, "normal")
    subject = (
        REASON_LABELS.get(reason, "Escalated conversation")
        + (f" — {complaint_type.replace('_', ' ')}" if complaint_type != "general_question" else "")
    )

    ticket = Ticket(
        reference=f"TKT-{secrets.token_hex(3).upper()}",
        customer_id=customer.id,
        conversation_id=conversation.id,
        subject=subject[:200],
        description=summary,
        category=complaint_type,
        priority=TicketPriority(priority_value),
        status=TicketStatus.escalated,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    return {
        "ticket_reference": ticket.reference,
        "priority": ticket.priority.value,
        "already_escalated": False,
    }


def _run_chat_turn(conversation: "Conversation", user_message: str, db) -> tuple["Message", list, dict | None]:
    """Stores the user's message (tagged with sentiment + complaint type),
    runs grounded generation with recent context - in an empathetic,
    solution-first tone when the message describes a real problem - stores
    the assistant's reply, and escalates to a human when the escalation
    rules say so. Returns the assistant's Message row, its citations, and
    escalation info (None if nothing was escalated). Shared by /chat,
    /chat/me and /chat/me/voice so all three stay in sync."""
    classification = classify_message(user_message)
    sentiment = classification["sentiment"]
    complaint_type = classification["complaint_type"]

    db.add(
        Message(
            conversation_id=conversation.id,
            sender_type=SenderType.customer,
            body=user_message,
            sentiment=sentiment,
            complaint_type=complaint_type,
        )
    )
    db.flush()

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

    reply_text, citations = generate_grounded_reply(
        llm_history, user_message, complaint_type=complaint_type
    )

    assistant_message = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.agent,
        sender_user_id=None,  # NULL = the bot, not a human agent
        body=reply_text,
        citations=citations,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    escalation_info = _maybe_escalate(
        conversation, user_message, reply_text, sentiment, complaint_type, recent, db
    )

    return assistant_message, citations, escalation_info


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

    assistant_message, citations, escalation = _run_chat_turn(conversation, payload.message, db)

    return jsonify(
        {
            "session_id": conversation.id,
            "reply": assistant_message.body,
            "citations": citations,
            "message_id": assistant_message.id,
            "escalation": escalation,
        }
    )


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


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/login")
def login():
    db = get_db()
    try:
        payload = LoginRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)

    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or not user.is_active:
        return api_error("Invalid email or password", 401)
    if not verify_password(payload.password, user.hashed_password):
        return api_error("Invalid email or password", 401)

    token = issue_token(user.id, user.email, user.role.value)
    return jsonify(
        {
            "token": token,
            "user": {"id": user.id, "name": user.full_name, "email": user.email, "role": user.role.value},
        }
    )


# --------------------------------------------------------------------------
# Customer-facing auth + chat dashboard (register, login, one chat per user,
# thumbs up/down feedback on AI replies).
# --------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    full_name: str
    email: str
    password: str


@app.post("/auth/register")
def register():
    db = get_db()
    try:
        payload = RegisterRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)

    if not payload.full_name.strip():
        return api_error("full_name cannot be empty", 400)
    if len(payload.password) < 6:
        return api_error("password must be at least 6 characters", 400)

    existing = db.scalar(select(Customer).where(Customer.email == payload.email))
    if existing:
        return api_error("An account with this email already exists", 409)

    customer = Customer(
        full_name=payload.full_name.strip(),
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    db.add(customer)
    db.commit()

    token = issue_token(customer.id, customer.email, "customer")
    return (
        jsonify(
            {
                "token": token,
                "user": {"id": customer.id, "name": customer.full_name, "email": customer.email},
            }
        ),
        201,
    )


@app.post("/auth/customer-login")
def customer_login():
    db = get_db()
    try:
        payload = LoginRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)

    customer = db.scalar(select(Customer).where(Customer.email == payload.email))
    if not customer or not customer.hashed_password:
        return api_error("Invalid email or password", 401)
    if not verify_password(payload.password, customer.hashed_password):
        return api_error("Invalid email or password", 401)

    token = issue_token(customer.id, customer.email, "customer")
    return jsonify(
        {
            "token": token,
            "user": {"id": customer.id, "name": customer.full_name, "email": customer.email},
        }
    )


def _get_or_create_my_conversation(customer_id: int, db) -> Conversation:
    """'1 user, 1 chat': always the customer's single ongoing conversation,
    never a new session per message like the testing /chat endpoint allows."""
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.customer_id == customer_id)
        .order_by(Conversation.started_at)
        .limit(1)
    )
    if conversation:
        return conversation
    conversation = Conversation(customer_id=customer_id)
    db.add(conversation)
    db.flush()
    return conversation


def _message_to_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "sender": m.sender_type.value,
        "body": m.body,
        "timestamp": m.sent_at.isoformat(),
        "feedback": m.feedback.value if m.feedback else None,
        "citations": m.citations or [],
        "sentiment": m.sentiment,
        "complaint_type": m.complaint_type,
    }


@app.get("/chat/me")
@require_customer
def my_chat_history():
    db = get_db()
    customer_id = int(g.current_customer["sub"])
    conversation = _get_or_create_my_conversation(customer_id, db)
    db.commit()

    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.sent_at, Message.id)
    ).all()

    open_ticket = _existing_open_ticket(conversation.id, db)

    return jsonify(
        {
            "session_id": conversation.id,
            "messages": [_message_to_dict(m) for m in messages],
            "open_ticket_reference": open_ticket.reference if open_ticket else None,
        }
    )


class MyChatRequest(BaseModel):
    message: str


@app.post("/chat/me")
@require_customer
def my_chat_send():
    db = get_db()
    customer_id = int(g.current_customer["sub"])

    try:
        payload = MyChatRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)
    if not payload.message.strip():
        return api_error("message cannot be empty", 400)

    conversation = _get_or_create_my_conversation(customer_id, db)
    assistant_message, citations, escalation = _run_chat_turn(conversation, payload.message, db)

    return jsonify(
        {
            "session_id": conversation.id,
            "reply": assistant_message.body,
            "citations": citations,
            "message_id": assistant_message.id,
            "escalation": escalation,
        }
    )


ALLOWED_AUDIO_EXTENSIONS = {"webm", "wav", "mp3", "m4a", "ogg"}
AUDIO_MIME_BY_EXT = {
    "webm": "audio/webm",
    "wav": "audio/wav",
    "mp3": "audio/mp3",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
}
MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB - a few minutes of speech


@app.post("/chat/me/voice")
@require_customer
def my_chat_send_voice():
    """
    Voice-input counterpart to /chat/me. Accepts a recorded audio clip
    (multipart field 'audio'), transcribes it with Gemini, then runs the
    transcript through the exact same _run_chat_turn() the text endpoint
    uses - so grounding, citations, and history all behave identically
    regardless of which "door" the message came in through.

    Text-to-speech for the reply is done client-side (Web Speech API) so
    this endpoint only ever returns text - see chat.js.
    """
    db = get_db()
    customer_id = int(g.current_customer["sub"])

    if "audio" not in request.files:
        return api_error("No audio provided (expected multipart field 'audio')", 400)

    audio_file = request.files["audio"]
    if not audio_file.filename:
        return api_error("Empty audio filename", 400)

    ext = audio_file.filename.rsplit(".", 1)[-1].lower() if "." in audio_file.filename else ""
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return api_error(
            f"Unsupported audio format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}",
            400,
        )

    audio_bytes = audio_file.read()
    if len(audio_bytes) > MAX_AUDIO_SIZE_BYTES:
        return api_error(f"Audio exceeds {MAX_AUDIO_SIZE_BYTES // (1024*1024)}MB limit", 400)
    if not audio_bytes:
        return api_error("Empty audio file", 400)

    transcript = transcribe_audio(audio_bytes, AUDIO_MIME_BY_EXT[ext])
    if not transcript:
        return api_error(
            "Couldn't hear anything in that recording — please try again.", 400
        )

    conversation = _get_or_create_my_conversation(customer_id, db)
    assistant_message, citations, escalation = _run_chat_turn(conversation, transcript, db)

    return jsonify(
        {
            "session_id": conversation.id,
            "transcript": transcript,
            "reply": assistant_message.body,
            "citations": citations,
            "message_id": assistant_message.id,
            "escalation": escalation,
        }
    )


class FeedbackRequest(BaseModel):
    feedback: str  # "good" | "bad"


@app.post("/chat/messages/<int:message_id>/feedback")
@require_customer
def submit_feedback(message_id: int):
    db = get_db()
    customer_id = int(g.current_customer["sub"])

    try:
        payload = FeedbackRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as e:
        return api_error(e.errors()[0]["msg"], 422)
    if payload.feedback not in ("good", "bad"):
        return api_error("feedback must be 'good' or 'bad'", 400)

    message = db.get(Message, message_id)
    if not message:
        return api_error("Message not found", 404)
    if message.sender_type != SenderType.agent:
        return api_error("Feedback can only be given on AI replies", 400)

    # ownership check: the message's conversation must belong to this customer
    conversation = db.get(Conversation, message.conversation_id)
    if not conversation or conversation.customer_id != customer_id:
        return api_error("Message not found", 404)

    message.feedback = FeedbackValue(payload.feedback)
    db.commit()

    return jsonify(_message_to_dict(message))


ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "docx", "txt", "md"}
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB


def _process_document_in_background(document_id: int) -> None:
    """Runs the full extract->chunk->embed->store pipeline on a separate
    thread with its OWN database session - the request's session (via
    flask.g) is torn down as soon as the request returns, so reusing it
    here would fail. This is what actually fixes documents getting stuck
    at 'processing' forever: previously this ran inside the request itself,
    so a slow/large file could outlast gunicorn's request timeout, the
    worker would be killed mid-request, and the status update to 'fail'
    never got written because the process died before reaching it."""
    from app.database import SessionLocal

    thread_db = SessionLocal()
    try:
        process_document(document_id, thread_db)
    finally:
        thread_db.close()


@app.post("/admin/documents/upload")
@require_admin
def upload_document():
    db = get_db()

    if "file" not in request.files:
        return api_error("No file provided (expected multipart field 'file')", 400)

    file = request.files["file"]
    if not file.filename:
        return api_error("Empty filename", 400)

    try:
        file_type = detect_file_type(file.filename)
    except ValueError as e:
        return api_error(str(e), 400)

    # store under a random name on disk; original name kept in the DB
    stored_name = f"{uuid.uuid4().hex}_{file.filename}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)
    file.save(stored_path)

    size = os.path.getsize(stored_path)
    if size > MAX_UPLOAD_SIZE_BYTES:
        os.remove(stored_path)
        return api_error(f"File exceeds {MAX_UPLOAD_SIZE_BYTES // (1024*1024)}MB limit", 400)

    document = Document(
        filename=file.filename,
        stored_path=stored_path,
        file_type=file_type,
        uploaded_by_id=int(g.current_user["sub"]),
        status=DocumentStatus.pending,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Returns immediately - processing continues in the background so a
    # large file can never time out the HTTP request. The frontend polls
    # GET /admin/documents to see status move from pending -> processing
    # -> success/fail.
    thread = threading.Thread(
        target=_process_document_in_background, args=(document.id,), daemon=True
    )
    thread.start()

    return jsonify(_document_to_dict(document)), 202


@app.get("/admin/documents")
@require_admin
def list_documents():
    db = get_db()
    documents = db.scalars(select(Document).order_by(Document.uploaded_at.desc())).all()
    return jsonify({"documents": [_document_to_dict(d) for d in documents]})


@app.post("/admin/documents/<int:document_id>/retry")
@require_admin
def retry_document(document_id: int):
    db = get_db()
    document = db.get(Document, document_id)
    if not document:
        return api_error("Document not found", 404)
    if document.status == DocumentStatus.success:
        return api_error("Document already processed successfully; nothing to retry", 400)

    thread = threading.Thread(
        target=_process_document_in_background, args=(document_id,), daemon=True
    )
    thread.start()

    document.status = DocumentStatus.pending
    db.commit()
    db.refresh(document)
    return jsonify(_document_to_dict(document)), 202


@app.get("/admin/documents/<int:document_id>/chunks")
@require_admin
def get_document_chunks(document_id: int):
    """Returns the actual stored chunk text for a document, so an admin
    can see what the file was split into and what content will be
    retrieved - not just a count."""
    db = get_db()
    document = db.get(Document, document_id)
    if not document:
        return api_error("Document not found", 404)

    if document.status != DocumentStatus.success:
        return jsonify(
            {
                "document_id": document_id,
                "filename": document.filename,
                "status": document.status.value,
                "chunks": [],
            }
        )

    collection = get_admin_collection()
    result = collection.get(
        where={"document_id": document_id},
        include=["documents", "metadatas"],
    )

    # Chroma doesn't guarantee order - sort by the chunk_index we stored.
    paired = sorted(
        zip(result["metadatas"], result["documents"]),
        key=lambda pair: pair[0]["chunk_index"],
    )

    return jsonify(
        {
            "document_id": document_id,
            "filename": document.filename,
            "status": document.status.value,
            "chunks": [
                {"chunk_index": meta["chunk_index"], "text": text} for meta, text in paired
            ],
        }
    )


def _document_to_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "filename": d.filename,
        "file_type": d.file_type,
        "status": d.status.value,
        "uploaded_at": d.uploaded_at.isoformat(),
        "processed_at": d.processed_at.isoformat() if d.processed_at else None,
        "chunk_count": d.chunk_count,
        "error_message": d.error_message,
        "uploaded_by": d.uploaded_by.full_name if d.uploaded_by else None,
    }


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

"""
Schema for a customer-support / order-management domain.

Relationship map (parent -> child):

    customers ──< orders ──< order_items >── products
        │           │  │
        │           │  └──< payments
        │           └─────< returns >── order_items
        │
        ├──< conversations ──< messages >── users
        └──< tickets >── conversations, orders, users
                            │
    users ──< action_logs ──┘
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

# Real JSONB on Postgres, plain JSON elsewhere (so tests can run on SQLite)
JSONType = JSON().with_variant(JSONB, "postgresql")
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enums  (native Postgres enum types)
# --------------------------------------------------------------------------

class OrderStatus(str, enum.Enum):
    pending = "pending"          # placed, not yet paid
    paid = "paid"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    refunded = "refunded"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"
    partially_refunded = "partially_refunded"


class PaymentMethod(str, enum.Enum):
    card = "card"
    bank_transfer = "bank_transfer"
    cash_on_delivery = "cash_on_delivery"
    wallet = "wallet"


class ReturnStatus(str, enum.Enum):
    requested = "requested"
    approved = "approved"
    rejected = "rejected"
    refunded = "refunded"


class UserRole(str, enum.Enum):
    agent = "agent"
    supervisor = "supervisor"
    admin = "admin"


class Channel(str, enum.Enum):
    email = "email"
    chat = "chat"
    phone = "phone"


class ConversationStatus(str, enum.Enum):
    open = "open"
    pending_customer = "pending_customer"
    closed = "closed"


class SenderType(str, enum.Enum):
    customer = "customer"
    agent = "agent"
    system = "system"


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    escalated = "escalated"
    resolved = "resolved"
    closed = "closed"


class TicketPriority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


# --------------------------------------------------------------------------
# Core commerce tables
# --------------------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    address_line: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.pending, nullable=False, index=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    shipping_address: Mapped[str | None] = mapped_column(String(255))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    returns: Mapped[list["Return"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    # the same product should appear only once per order (bump quantity instead)
    __table_args__ = (UniqueConstraint("order_id", "product_id", name="uq_order_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT: never let a product be deleted out from under a historical order
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # price is COPIED at purchase time - product price may change later
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="order_items")
    returns: Mapped[list["Return"]] = relationship(back_populates="order_item")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="payment_method"), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.pending, nullable=False, index=True
    )
    transaction_ref: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped["Order"] = relationship(back_populates="payments")


class Return(Base):
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # denormalised link to the exact line being returned
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus, name="return_status"), default=ReturnStatus.requested, nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped["Order"] = relationship(back_populates="returns")
    order_item: Mapped["OrderItem"] = relationship(back_populates="returns")


# --------------------------------------------------------------------------
# Support-side tables
# --------------------------------------------------------------------------

class User(Base):
    """Internal staff (support agents), NOT customers."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), default=UserRole.agent, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="sender_user")
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="assigned_user")
    action_logs: Mapped[list["ActionLog"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel, name="channel"), default=Channel.chat, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        default=ConversationStatus.open,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.sent_at"
    )
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_type: Mapped[SenderType] = mapped_column(Enum(SenderType, name="sender_type"), nullable=False)
    # NULL when the sender is the customer or the system
    sender_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sender_user: Mapped["User | None"] = relationship(back_populates="messages")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # a ticket may be raised without a chat thread, or about no specific order
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"), default=TicketPriority.normal, nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"), default=TicketStatus.open, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="tickets")
    conversation: Mapped["Conversation | None"] = relationship(back_populates="tickets")
    order: Mapped["Order | None"] = relationship(back_populates="tickets")
    assigned_user: Mapped["User | None"] = relationship(back_populates="tickets")
    action_logs: Mapped[list["ActionLog"]] = relationship(back_populates="ticket")


class ActionLog(Base):
    """Append-only audit trail. Every state change anything makes lands here."""

    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    user: Mapped["User | None"] = relationship(back_populates="action_logs")
    ticket: Mapped["Ticket | None"] = relationship(back_populates="action_logs")

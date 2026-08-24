"""
Seed the database with realistic fake data.

Run:  docker compose exec api python -m app.seed
Wipe and reseed:  docker compose exec api python -m app.seed --reset
"""

from __future__ import annotations

import random
import sys
from datetime import timedelta
from decimal import Decimal

from faker import Faker
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import (
    ActionLog,
    Channel,
    Conversation,
    ConversationStatus,
    Customer,
    Message,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
    Return,
    ReturnStatus,
    SenderType,
    Ticket,
    TicketPriority,
    TicketStatus,
    User,
    UserRole,
    utcnow,
)

fake = Faker()
Faker.seed(42)
random.seed(42)

CATEGORIES = ["Electronics", "Home", "Apparel", "Beauty", "Sports", "Books"]


def money(value) -> Decimal:
    """Round to 2dp the way money should be rounded."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------

def seed_users(db) -> list[User]:
    users = [
        User(
            full_name="Admin User",
            email="admin@example.com",
            hashed_password="!placeholder-replace-with-bcrypt",
            role=UserRole.admin,
        ),
        User(
            full_name="Sara Supervisor",
            email="sara@example.com",
            hashed_password="!placeholder-replace-with-bcrypt",
            role=UserRole.supervisor,
        ),
    ]
    for _ in range(4):
        users.append(
            User(
                full_name=fake.name(),
                email=fake.unique.email(),
                hashed_password="!placeholder-replace-with-bcrypt",
                role=UserRole.agent,
            )
        )
    db.add_all(users)
    db.flush()
    return users


def seed_customers(db, n: int = 30) -> list[Customer]:
    customers = [
        Customer(
            full_name=fake.name(),
            email=fake.unique.email(),
            phone=fake.phone_number()[:40],
            address_line=fake.street_address()[:255],
            city=fake.city(),
            country=fake.country(),
            created_at=fake.date_time_between(start_date="-2y", end_date="-30d", tzinfo=None),
        )
        for _ in range(n)
    ]
    db.add_all(customers)
    db.flush()
    return customers


def seed_products(db, n: int = 20) -> list[Product]:
    products = []
    for i in range(n):
        products.append(
            Product(
                sku=f"SKU-{1000 + i}",
                name=fake.catch_phrase()[:160],
                description=fake.paragraph(nb_sentences=3),
                category=random.choice(CATEGORIES),
                price=money(random.uniform(5, 900)),
                stock_quantity=random.randint(0, 300),
                is_active=random.random() > 0.1,   # ~10% discontinued
            )
        )
    db.add_all(products)
    db.flush()
    return products


def build_order(db, customer: Customer, products: list[Product], number: int) -> Order:
    """One normal order: 1-4 distinct line items, priced at purchase time."""
    placed = fake.date_time_between(start_date="-1y", end_date="now", tzinfo=None)
    order = Order(
        order_number=f"ORD-{20000 + number}",
        customer_id=customer.id,
        status=OrderStatus.pending,
        shipping_address=customer.address_line,
        placed_at=placed,
    )
    db.add(order)
    db.flush()

    chosen = random.sample(products, k=random.randint(1, 4))
    total = Decimal("0.00")
    for product in chosen:
        qty = random.randint(1, 3)
        unit = product.price
        line = money(unit * qty)
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=qty,
                unit_price=unit,
                line_total=line,
            )
        )
        total += line

    order.total_amount = money(total)
    db.flush()
    return order


def advance_lifecycle(db, order: Order) -> None:
    """Push a pending order along a plausible happy path and pay for it."""
    roll = random.random()
    if roll < 0.45:
        order.status = OrderStatus.delivered
        order.shipped_at = order.placed_at + timedelta(days=random.randint(1, 3))
        order.delivered_at = order.shipped_at + timedelta(days=random.randint(1, 6))
    elif roll < 0.75:
        order.status = OrderStatus.shipped
        order.shipped_at = order.placed_at + timedelta(days=random.randint(1, 3))
    else:
        order.status = OrderStatus.paid

    db.add(
        Payment(
            order_id=order.id,
            amount=order.total_amount,
            method=random.choice(list(PaymentMethod)),
            status=PaymentStatus.completed,
            transaction_ref=fake.unique.bothify("TXN-########"),
            created_at=order.placed_at,
            paid_at=order.placed_at + timedelta(minutes=random.randint(1, 90)),
        )
    )


# --------------------------------------------------------------------------
# The awkward cases - these are what break naive code later
# --------------------------------------------------------------------------

def make_cancelled_order(db, order: Order, users: list[User]) -> None:
    """Cancelled after payment -> money must go back, stock never shipped."""
    order.status = OrderStatus.cancelled
    order.cancelled_at = order.placed_at + timedelta(hours=random.randint(2, 48))

    db.add(
        Payment(
            order_id=order.id,
            amount=order.total_amount,
            refunded_amount=order.total_amount,
            method=PaymentMethod.card,
            status=PaymentStatus.refunded,
            transaction_ref=fake.unique.bothify("TXN-########"),
            created_at=order.placed_at,
            paid_at=order.placed_at + timedelta(minutes=20),
        )
    )
    db.add(
        ActionLog(
            user_id=random.choice(users).id,
            action_type="order.cancelled",
            entity_type="order",
            entity_id=order.id,
            payload={"reason": "customer_request", "refunded": str(order.total_amount)},
            created_at=order.cancelled_at,
        )
    )


def make_partial_refund(db, order: Order, users: list[User]) -> None:
    """Delivered, then ONE line item returned. Order total stays intact;
    the payment carries a refunded_amount smaller than amount."""
    order.status = OrderStatus.delivered
    order.shipped_at = order.placed_at + timedelta(days=2)
    order.delivered_at = order.shipped_at + timedelta(days=3)

    items = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    item = items[0]
    return_qty = 1
    refund = money(item.unit_price * return_qty)

    db.add(
        Payment(
            order_id=order.id,
            amount=order.total_amount,
            refunded_amount=refund,
            method=PaymentMethod.card,
            status=PaymentStatus.partially_refunded,
            transaction_ref=fake.unique.bothify("TXN-########"),
            created_at=order.placed_at,
            paid_at=order.placed_at + timedelta(minutes=12),
        )
    )
    db.add(
        Return(
            order_id=order.id,
            order_item_id=item.id,
            quantity=return_qty,
            reason="Item arrived damaged",
            refund_amount=refund,
            status=ReturnStatus.refunded,
            requested_at=order.delivered_at + timedelta(days=1),
            resolved_at=order.delivered_at + timedelta(days=4),
        )
    )
    db.add(
        ActionLog(
            user_id=random.choice(users).id,
            action_type="return.refunded",
            entity_type="order",
            entity_id=order.id,
            payload={"partial": True, "amount": str(refund), "order_total": str(order.total_amount)},
            created_at=order.delivered_at + timedelta(days=4),
        )
    )


def make_unpaid_order(db, order: Order) -> None:
    """Placed, never paid. A failed payment attempt exists but no completed one."""
    order.status = OrderStatus.pending
    db.add(
        Payment(
            order_id=order.id,
            amount=order.total_amount,
            method=PaymentMethod.card,
            status=PaymentStatus.failed,
            transaction_ref=fake.unique.bothify("TXN-########"),
            created_at=order.placed_at,
            paid_at=None,
        )
    )


def seed_support(db, customers: list[Customer], orders: list[Order], users: list[User]) -> None:
    """Conversations, messages and tickets hanging off real customers/orders."""
    agents = [u for u in users if u.role == UserRole.agent]

    for customer in random.sample(customers, k=15):
        started = fake.date_time_between(start_date="-6m", end_date="now", tzinfo=None)
        closed = random.random() < 0.6
        conv = Conversation(
            customer_id=customer.id,
            channel=random.choice(list(Channel)),
            subject=fake.sentence(nb_words=6)[:200],
            status=ConversationStatus.closed if closed else ConversationStatus.open,
            started_at=started,
            closed_at=started + timedelta(hours=random.randint(1, 72)) if closed else None,
        )
        db.add(conv)
        db.flush()

        agent = random.choice(agents)
        cursor = started
        for turn in range(random.randint(2, 6)):
            is_customer = turn % 2 == 0
            cursor += timedelta(minutes=random.randint(2, 240))
            db.add(
                Message(
                    conversation_id=conv.id,
                    sender_type=SenderType.customer if is_customer else SenderType.agent,
                    sender_user_id=None if is_customer else agent.id,
                    body=fake.paragraph(nb_sentences=2),
                    sent_at=cursor,
                )
            )

        # ~half of conversations escalate into a ticket
        if random.random() < 0.5:
            customer_orders = [o for o in orders if o.customer_id == customer.id]
            resolved = random.random() < 0.5
            db.add(
                Ticket(
                    reference=fake.unique.bothify("TKT-#####"),
                    customer_id=customer.id,
                    conversation_id=conv.id,
                    order_id=random.choice(customer_orders).id if customer_orders else None,
                    assigned_user_id=agent.id,
                    subject=fake.sentence(nb_words=5)[:200],
                    category=random.choice(["billing", "delivery", "refund", "product", "account"]),
                    priority=random.choice(list(TicketPriority)),
                    status=TicketStatus.resolved if resolved else TicketStatus.open,
                    created_at=started,
                    resolved_at=started + timedelta(days=random.randint(1, 5)) if resolved else None,
                )
            )


# --------------------------------------------------------------------------

def run(reset: bool = False) -> None:
    if reset:
        print("Dropping all tables...")
        Base.metadata.drop_all(engine)

    print("Creating tables...")
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        if db.scalar(select(Customer).limit(1)):
            print("Database already has data. Use --reset to wipe and reseed.")
            return

        users = seed_users(db)
        customers = seed_customers(db, 30)
        products = seed_products(db, 20)
        print(f"  users:     {len(users)}")
        print(f"  customers: {len(customers)}")
        print(f"  products:  {len(products)}")

        orders: list[Order] = []
        for i in range(50):
            orders.append(build_order(db, random.choice(customers), products, i))

        # first three orders get the awkward treatment, the rest are normal
        make_cancelled_order(db, orders[0], users)
        make_partial_refund(db, orders[1], users)
        make_unpaid_order(db, orders[2])
        for order in orders[3:]:
            advance_lifecycle(db, order)

        db.flush()
        seed_support(db, customers, orders, users)

        db.commit()
        print(f"  orders:    {len(orders)}  (1 cancelled, 1 partially refunded, 1 unpaid)")
        print("Seed complete.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)

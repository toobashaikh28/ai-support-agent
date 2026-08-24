from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import Base, engine, get_db
from app.models import Customer, Order, OrderItem, OrderStatus, Product, Ticket

app = FastAPI(title="Support API", version="0.1.0")


@app.on_event("startup")
def on_startup() -> None:
    # Fine for day 1. Swap for Alembic migrations before this schema changes.
    Base.metadata.create_all(engine)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "database": "connected"}


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
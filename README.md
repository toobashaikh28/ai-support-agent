# Support API — Day 1

FastAPI + Postgres scaffolding with a seeded relational schema.

## Run it

```bash
docker compose up --build
```

Then seed the database (in a second terminal):

```bash
docker compose exec api python -m app.seed
```

Check it worked:

- http://localhost:8000/health → `{"status":"ok","database":"connected"}`
- http://localhost:8000/stats → row counts + orders grouped by status
- http://localhost:8000/docs → Swagger UI

## Useful commands

```bash
docker compose exec api python -m app.seed --reset   # wipe and reseed
docker compose logs -f api                           # tail API logs
docker compose exec db psql -U appuser -d supportdb  # SQL shell
docker compose down                                  # stop
docker compose down -v                               # stop AND delete the database volume
```

## Schema

Eleven tables in three groups.

**Commerce:** `customers` → `orders` → `order_items` → `products`, with
`payments` and `returns` hanging off orders.

**Support:** `users` (staff, not customers), `conversations` → `messages`,
and `tickets` linking a customer to an optional conversation and order.

**Audit:** `action_logs`, append-only.

### Decisions worth remembering

| Decision | Why |
|---|---|
| `order_items.unit_price` copies the price at purchase time | Product prices change. Joining to `products.price` for an old order gives the wrong historical total. |
| `order_items` FK to products is `RESTRICT` | A product must never be deletable out from under a historical order. |
| `orders`, `conversations`, `messages` cascade from their parent | Deleting a customer should take their whole trail with it. |
| `tickets.order_id`, `.conversation_id`, `.assigned_user_id` are nullable + `SET NULL` | A ticket can exist unassigned, about no order, raised outside a chat. |
| `payments.refunded_amount` is separate from `amount` | Partial refunds need both numbers. One column can't express "paid 4526.17, gave back 632.69". |
| Money is `Numeric(10,2)`, never float | Float arithmetic loses cents. |
| Enums are native Postgres types | The database rejects an invalid status, not just the application. |

## Seed data

30 customers, 20 products, 50 orders, plus 6 staff users, 15 conversations
with messages, and ~9 tickets.

Seeded with `Faker.seed(42)` so the data is identical on every machine —
useful when comparing behaviour with a teammate.

Three deliberate edge cases, always the first three orders:

| Order | Case | What makes it awkward |
|---|---|---|
| `ORD-20000` | Cancelled | Was paid, then fully refunded. Status is `cancelled` but a completed payment exists in history. |
| `ORD-20001` | Partial refund | Delivered, one line item returned. Order total unchanged; only `refunded_amount` moves. Code that assumes refund == total will break here. |
| `ORD-20002` | Unpaid | A `failed` payment row with `paid_at = NULL`. Code that checks "has a payment row" instead of "has a *completed* payment" will wrongly treat this as paid. |

## Next

`Base.metadata.create_all()` on startup is fine today, but it cannot alter an
existing table. Add Alembic before the first schema change.

Replace the `hashed_password` placeholders with real bcrypt hashes before any
auth work.

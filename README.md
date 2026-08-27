# Support API

Flask + Postgres backend for an AI customer-support agent: RAG-grounded
chat, an admin document-upload pipeline, and the underlying commerce
schema it all sits on.

## Run it

```bash
docker compose up --build -d
docker compose exec api python -m app.seed
docker compose exec api python -m app.rag.ingest
```

Then check:

- http://localhost:8000/health → `{"status":"ok","database":"connected"}`
- http://localhost:8000/stats → row counts + orders grouped by status
- http://localhost:8000/admin → admin login page (see credentials below)

There's no auto-generated API docs page (that was a FastAPI feature; this
project runs on Flask). Test endpoints with Postman or `curl`.

## Admin login

```
email:    admin@example.com
password: admin123
```

Change this before deploying anywhere real - it's a seeded dev password.

## Useful commands

```bash
docker compose exec api python -m app.seed --reset      # wipe and reseed
docker compose exec api python -m app.rag.ingest --reset   # re-ingest policy docs
docker compose logs -f api                               # tail API logs
docker compose exec db psql -U appuser -d supportdb      # SQL shell
docker compose down                                       # stop
docker compose down -v                                    # stop AND delete all volumes (db, vectors, uploads)
```

## Architecture

### Commerce + support schema (Day 1)

Eleven tables: `customers`, `products`, `orders`, `order_items`, `payments`,
`returns`, `conversations`, `messages`, `tickets`, `users`, `action_logs`.
See inline comments in `app/models.py` for the relationship map and the
reasoning behind each foreign-key/cascade choice.

### Chat with memory (Day 2)

`POST /chat` — takes `{message, session_id?, customer_id?}`. Starts a new
session if no `session_id` is given. Every message is stored in `messages`;
the last 10 messages of a session are replayed to the LLM (Gemini) as
context on every turn.

### Policy RAG pipeline (Day 3)

Static policy docs in `app/data/policies/*.md` → chunked → embedded via
**Gemini's embedding API** → stored in a Chroma collection called
`policies`. Triggered manually via `python -m app.rag.ingest`. Query it
directly with `GET /rag/search?q=...`.

### Admin document upload (this feature)

A second, independent ingestion pipeline for admin-uploaded files (PDF,
Word, .txt, .md), with full status tracking and retry.

**Flow:** admin logs in (JWT) → uploads a file through the browser →
a `documents` row is created (`status=pending`) → text is extracted →
chunked with LangChain's `RecursiveCharacterTextSplitter` → embedded with
a **local, free, open-source model** (`BAAI/bge-small-en-v1.5` via
`fastembed`, no API key, no cost) → stored in a *separate* Chroma
collection called `admin_documents` → the row is updated to
`success` or `fail`.

**Why two separate embedding pipelines and two separate Chroma
collections:** Gemini's embeddings and the local model's embeddings are
different sizes and live in incompatible vector spaces - they cannot be
mixed in one collection. Policy docs (curated, static, high-value) use the
paid Gemini API for quality; admin uploads (variable format, potentially
high volume) use a free local model to avoid per-document API cost.

**On failure:** the pipeline catches any error (bad file, extraction
failure, embedding outage) and writes it to `documents.error_message`
with `status=fail`. The row is never left half-processed - a failed
attempt is safe to retry with `POST /admin/documents/<id>/retry`, which
re-runs the whole pipeline from scratch and cleans up any partial vectors
from the prior attempt first.

**Endpoints:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | — | `{email, password}` → JWT |
| POST | `/admin/documents/upload` | admin | multipart `file` field |
| GET | `/admin/documents` | admin | list all uploads + status |
| POST | `/admin/documents/<id>/retry` | admin | re-run a failed document |

**Frontend:** plain HTML/CSS/JS at `/admin` (redirects to
`/static/admin/login.html`). Drag-and-drop upload, a live status table,
retry buttons on failed rows. JWT stored in `localStorage`.

### Retrieval, grounding & citations (Day 4)

`POST /chat` now actually uses both knowledge bases instead of answering
from Gemini's general knowledge alone. On every message:

1. `app/rag/retrieval.py` runs top-k retrieval against **both** Chroma
   collections separately (policies via Gemini, admin_documents via the
   local model), each filtered against its own relevance-distance
   threshold. Sources are never merged into one ranked list, since their
   distance scores aren't comparable to each other - see the module
   docstring for why.
2. `app/rag/chat_grounding.py` builds a system prompt that instructs the
   model to answer factual/policy questions ONLY from the retrieved
   context, refuse honestly when nothing relevant was found, but still
   engage normally with greetings/small talk that don't need grounding.
3. The chat response now includes a `citations` array - every chunk that
   was actually given to the model, tagged with its source file, chunk
   index, which collection it came from, and its raw relevance distance.

**Tuning the relevance threshold:** `POLICY_DISTANCE_THRESHOLD` and
`ADMIN_DISTANCE_THRESHOLD` (env vars, default `0.9`) control how close a
chunk must be to count as relevant. Every citation includes its raw
distance specifically so this can be tuned by watching real queries
rather than guessing - if real questions are being wrongly refused, raise
the threshold; if the model starts citing marginally-related chunks,
lower it.



### Customer chat dashboard (this feature)

A self-service login for actual customers, separate from the admin panel -
register, log in, and chat with the AI, with thumbs up/down feedback on
each AI reply.

**"1 user, 1 chat":** unlike the testing `/chat` endpoint (which can start
a new session per call via `session_id`), a customer here always has
exactly one ongoing conversation. `_get_or_create_my_conversation()`
finds their existing conversation or creates it once; every message after
that lands in the same thread.

**Auth:** customers authenticate against the `customers` table (the same
table Day 1's order data lives in), via a new nullable `hashed_password`
column - registering creates a real login-capable customer row.
Deliberately kept separate from the admin's JWT (`role: customer` vs
`role: admin`); a `require_customer` decorator enforces this, and an
admin's token is rejected on customer routes and vice versa.

**Feedback:** `messages.feedback` is nullable (`null` / `good` / `bad`),
settable only on AI replies (not the customer's own messages), and only
by the conversation's owner - verified server-side, not just hidden in
the UI.

**Endpoints:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | — | `{full_name, email, password}` → JWT |
| POST | `/auth/customer-login` | — | `{email, password}` → JWT |
| GET | `/chat/me` | customer | full history of their one chat, as JSON |
| POST | `/chat/me` | customer | `{message}` → grounded reply + citations |
| POST | `/chat/messages/<id>/feedback` | customer | `{feedback: "good"\|"bad"}` |

**Frontend:** `/app` → `/static/user/` - register, login, and a chat UI
with message bubbles, citation tags under grounded replies, and
thumbs-up/down buttons under every AI message.

### Decisions worth remembering

| Decision | Why |
|---|---|
| `order_items.unit_price` copies the price at purchase time | Product prices change; joining to current price would corrupt historical totals. |
| `payments.refunded_amount` is separate from `amount` | One column can't express "paid 4526.17, gave back 632.69" for a partial refund. |
| Money is `Numeric(10,2)`, never float | Float arithmetic loses cents. |
| Two Chroma collections, two embedding providers | Vector dimensions differ between Gemini and the local model; they cannot share a collection. |
| Document pipeline wraps everything in try/except at the top level | A retry must be safe to call blindly - it re-runs cleanly rather than needing to know what step failed. |
| `fastembed` instead of `sentence-transformers` | The latter pulls in PyTorch's CUDA dependency chain, which is fragile in a plain CPU container. `fastembed` uses ONNX Runtime directly - lighter, fewer moving parts. |

## Seed data

30 customers, 20 products, 50 orders (including one cancelled, one
partially refunded, one unpaid), 6 staff users (one admin, one supervisor,
four agents - all with real, working passwords), 15 support conversations.

## Next

- `Base.metadata.create_all()` on startup can't alter an existing table.
  Add Alembic before changing the schema further.
- JWT auth here is intentionally minimal (single secret, no refresh
  tokens, no rate limiting on login). Fine for an admin panel; harden
  before any customer-facing auth.
- The local embedding model downloads on first use (~130MB from Hugging
  Face) - the first document upload after a fresh `docker compose up`
  will be slower than subsequent ones while it downloads. It's cached in
  the `fastembed_cache` volume after that.

# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

A **Claude-powered chatbot** with company-document RAG and per-user memory,
intended to eventually run over WhatsApp.

- **LLM:** Claude via the official `anthropic` Python SDK.
- **Company knowledge:** RAG over PDFs in `docs/` → local FAISS vector DB
  (`vector_db/`), retrieved with **local, free** sentence-transformers
  embeddings (not an API — Anthropic has no embeddings endpoint).
- **Per-user memory:** PostgreSQL keyed by **phone number** (psycopg3 +
  connection pool, `DATABASE_URL`). Stores a JSONB `profile` of personal facts
  the user shares + full message history. `docker compose up -d` runs local PG.
- Build/test in the terminal first; WhatsApp wiring comes later.

## Architecture (the current/active path)

```
PDFs (docs/) ─ ingest.py ─> local embeddings ─> vector_db/  (company knowledge)
                                                     │
                              mcp_server.py  ◄───── search_company_docs (MCP tool)
                                   ▲ stdio                │
                                   │ (mcp_client.py)      │
phone + message ─> chatbot.answer() ─ tool-use loop ──────┤
                       │                                  ▼
        store.py (Postgres: profile + history) ─────────> Claude ─> reply
                       ▲                                  │
                       └──── save_personal_info ◄─────────┘  (Claude persists new facts)
```

Claude reaches company knowledge by **calling the `search_company_docs` MCP tool**
(no longer pre-injected into the prompt); personal facts still go through the
local `save_personal_info` tool. Both are handled in the one tool-use loop.

| File | Role |
|------|------|
| `chatbot.py` | **The brain.** `answer(phone, message)`: loads history + profile, calls Claude with a manual tool-use loop over two tools — `save_personal_info` (local Postgres) and the MCP `search_company_docs` (company RAG) — persists facts, logs messages. |
| `mcp_server.py` | **Standalone MCP server** (FastMCP, stdio) exposing `search_company_docs(query, top_k)` — RAG retrieval over `vector_db/`. Loads embeddings + FAISS once. Reusable by any MCP client (e.g. Claude Desktop). Never calls an LLM. |
| `mcp_client.py` | Sync↔async bridge: a background asyncio loop holds one persistent stdio `ClientSession` to `mcp_server.py`. `get_tools()` / `call_tool()` let the synchronous, Flask-threaded brain use MCP tools. |
| `store.py` | PostgreSQL layer (psycopg3 + pool). `users(phone PK, name, profile JSONB, created_at)`, `messages(...)`. Helpers: `get_or_create_user`, `get_profile`, `update_profile`, `log_message`, `get_recent_messages`. Phone numbers are normalized (`normalize_number`) before use. |
| `ingest.py` | Builds `vector_db/` from `docs/*.pdf`. Re-run after changing docs. |
| `test_chat.py` | Terminal chat REPL; "log in" with a phone number. |
| `main.py` | Twilio WhatsApp webhook → `chatbot.answer(sender, text)`. |
| `main_meta.py` | Meta Cloud API WhatsApp webhook → `chatbot.answer(sender, text)`. |

## Commands

```powershell
venv\Scripts\activate              # activate venv (Windows)
pip install -r requirements.txt
docker compose up -d               # start local PostgreSQL (store backend)
python ingest.py                   # (re)build vector_db/ from docs/
python mcp_server.py               # run the company-docs MCP server alone (stdio)
python test_chat.py                # chat in terminal (needs ANTHROPIC_API_KEY + Postgres)
python main.py                     # Twilio webhook (dev server, port 5000)
waitress-serve --listen=0.0.0.0:5000 main:app   # production WSGI server
```

There is no test suite. `store.py` needs a reachable Postgres (`DATABASE_URL`);
the FAISS retriever can be exercised without an API key.

## Configuration (`.env`, git-ignored)

- `ANTHROPIC_API_KEY` — **required** for any live chat.
- `ANTHROPIC_MODEL` — default `claude-opus-4-8`. Do **not** silently downgrade
  for cost; switching to `claude-haiku-4-5` / `claude-sonnet-4-6` is the user's
  decision.
- `COMPANY_NAME`, `DB_PATH`, `HF_EMBEDDING_MODEL`.

## Conventions & gotchas

- **Claude API:** official `anthropic` SDK only. Manual tool-use loop lives in
  `chatbot.answer` — keep it. System prompt is a 2-block list: a **stable**
  instruction block with `cache_control` (prompt caching) followed by the
  **volatile** per-request context (the user profile). Don't move volatile
  content before the cache breakpoint.
- **Company knowledge is an MCP tool, not prompt-injected.** Claude calls
  `search_company_docs` (served by `mcp_server.py`, bridged by `mcp_client.py`)
  when it needs docs. To add more company-side capabilities, add tools to
  `mcp_server.py` — `chatbot.py` auto-discovers them via `mcp_client.get_tools()`
  and routes any non-`save_personal_info` tool call through `mcp_client.call_tool`.
- `mcp_server.py` must keep **stdout clean** (it's the MCP stdio transport) —
  log to stderr only.
- Parse tool inputs as structured data (`block.input`), never string-match.
- **Graceful failure:** `store.*` calls in `answer()` are wrapped — a DB error
  returns `_DB_BUSY_MSG` ("systems are updating") instead of crashing the reply
  (maps to Aayiq's `PASS_TO_INBOX`). Only `anthropic.APIError` is caught for the
  LLM call. Reply-logging is best-effort.
- **Validate before write:** `save_personal_info` runs `store.validate_personal_facts`
  (E.164 phone / email format) before persisting; invalid values are returned to
  Claude as a tool_result so it asks the user to correct them — nothing is saved.
- **WhatsApp UX (in the system prompt):** `*single-asterisk*` bold (not `**`),
  numbered lists for choices, replies under ~150 words. New users get a light
  discovery loop (ask name); returning users are greeted by name. `answer()`
  still truncates to 1500 chars as a backstop.
- **Phone number is the identity key** everywhere. Always normalize.
- `store.py` uses psycopg3: `%s` placeholders (not `?`), JSONB profile via
  `psycopg.types.json.Json`, a module-level `ConnectionPool`. `DATABASE_URL` is
  the plain libpq form (`postgresql://…`), NOT SQLAlchemy's `postgresql+psycopg://…`.

## Production

`python main.py` runs Flask's dev server (fine for local/ngrok testing). For
production use the bundled waitress WSGI server:
`waitress-serve --listen=0.0.0.0:5000 main:app` (or `main_meta:app`). Front it
with real HTTPS, use a permanent Meta token, keep secrets in env/secret manager,
and point `DATABASE_URL` at a managed Postgres (the `docker-compose.yml` PG is
for local dev only).

The old HuggingFace + per-user-pgvector files (`rag.py`, `rag_multi.py`, `db.py`,
`ingest_user.py`, `main_meta_multi.py`) were **removed** in the production
cleanup — don't reintroduce them. (`docker-compose.yml` was re-added for the
Postgres store and is current.)

## Roadmap (per the user)

1. ✅ Build the Claude chatbot (RAG + per-user memory + history).
2. ✅ MCP integration for company docs — `search_company_docs` is served by
   `mcp_server.py` (stdio) and plugged into the tool-use loop in `chatbot.py`
   via `mcp_client.py`.
3. ✅ Wire to WhatsApp (Twilio + Meta) reusing `chatbot.answer`.

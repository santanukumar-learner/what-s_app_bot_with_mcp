# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

A **Claude-powered chatbot** with company-document RAG and per-user memory,
intended to eventually run over WhatsApp.

- **LLM:** Claude via the official `anthropic` Python SDK.
- **Company knowledge:** RAG over PDFs in `docs/` → local FAISS vector DB
  (`vector_db/`), retrieved with **local, free** sentence-transformers
  embeddings (not an API — Anthropic has no embeddings endpoint).
- **Per-user memory:** SQLite (`chatbot.db`) keyed by **phone number**. Stores a
  JSON `profile` of personal facts the user shares + full message history.
- Build/test in the terminal first; WhatsApp wiring comes later.

## Architecture (the current/active path)

```
PDFs (docs/) ─ ingest.py ─> local embeddings ─> vector_db/  (company knowledge)
                                                     │
phone + message ─> chatbot.answer() ─ retrieve ──────┤
                       │                             ▼
        store.py (SQLite: profile + history) ─────> Claude ─> reply
                       ▲                             │
                       └──── save_personal_info ◄────┘  (Claude persists new facts)
```

| File | Role |
|------|------|
| `chatbot.py` | **The brain.** `answer(phone, message)`: loads history + profile, RAG-retrieves company context, calls Claude with a manual tool-use loop, persists facts via the `save_personal_info` tool, logs messages. |
| `store.py` | SQLite layer. `users(phone PK, name, profile JSON, created_at)`, `messages(...)`. Helpers: `get_or_create_user`, `get_profile`, `update_profile`, `log_message`, `get_recent_messages`. Phone numbers are normalized (`normalize_number`) before use. |
| `ingest.py` | Builds `vector_db/` from `docs/*.pdf`. Re-run after changing docs. |
| `test_chat.py` | Terminal chat REPL; "log in" with a phone number. |
| `main.py` | Twilio WhatsApp webhook → `chatbot.answer(sender, text)`. |
| `main_meta.py` | Meta Cloud API WhatsApp webhook → `chatbot.answer(sender, text)`. |

## Commands

```powershell
venv\Scripts\activate              # activate venv (Windows)
pip install -r requirements.txt
python ingest.py                   # (re)build vector_db/ from docs/
python test_chat.py                # chat in terminal (needs ANTHROPIC_API_KEY)
python main.py                     # Twilio webhook (dev server, port 5000)
waitress-serve --listen=0.0.0.0:5000 main:app   # production WSGI server
```

There is no test suite. To smoke-test non-LLM logic, exercise `store.py` and the
FAISS retriever directly (they need no API key).

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
  **volatile** per-request context (company chunks + user profile). Don't move
  volatile content before the cache breakpoint.
- Parse tool inputs as structured data (`block.input`), never string-match.
- Keep replies short (WhatsApp); `answer()` truncates to 1500 chars.
- **Phone number is the identity key** everywhere. Always normalize.
- `chatbot.db` and `*.db` are git-ignored (personal data) — never commit them.

## Production

`python main.py` runs Flask's dev server (fine for local/ngrok testing). For
production use the bundled waitress WSGI server:
`waitress-serve --listen=0.0.0.0:5000 main:app` (or `main_meta:app`). Front it
with real HTTPS, use a permanent Meta token, keep secrets in env/secret manager,
and back up `chatbot.db`. To scale past one box, replace SQLite in `store.py`.

The old HuggingFace + Postgres/pgvector files (`rag.py`, `rag_multi.py`, `db.py`,
`ingest_user.py`, `main_meta_multi.py`, `docker-compose.yml`) were **removed** in
the production cleanup — don't reintroduce them.

## Roadmap (per the user)

1. ✅ Build the Claude chatbot (RAG + per-user memory + history).
2. ⏳ MCP integration for company docs — plug MCP tools into the existing
   tool-use loop in `chatbot.py` (waiting on the user's MCP docs).
3. ✅ Wire to WhatsApp (Twilio + Meta) reusing `chatbot.answer`.

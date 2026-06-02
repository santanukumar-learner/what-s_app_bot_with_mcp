# Claude WhatsApp Chatbot (with per-user memory)

A chatbot powered by **Claude** (Anthropic) that:

- **answers company questions** grounded in your own PDF documents (RAG over a
  local **FAISS** vector DB built with free local embeddings), exposed to Claude
  as an **MCP tool** (`search_company_docs`) it calls on demand, and
- **remembers each user personally** — when someone tells the bot a fact about
  themselves (name, email, role, city…), it's saved to **PostgreSQL** keyed by
  their **phone number**, and recalled in future chats, and
- **stores the full message history** per user.

> Build & test the chatbot first in the terminal (`test_chat.py`). WhatsApp
> wiring (Twilio / Meta) comes after and reuses the same brain.

```
Your PDFs (docs/) ─> ingest.py ─> local embeddings ─> FAISS vector_db/  (company knowledge)
                                                            │
                          mcp_server.py (MCP) ◄──── search_company_docs (Claude calls it)
                                  ▲ stdio                   │
phone + message ─> chatbot.answer() ─ tool-use loop ────────┤
                         │                                  ▼
      PostgreSQL (profile + history, keyed by phone) ─────> Claude ─> reply
                         ▲                                  │
                         └──── save_personal_info ◄─────────┘  (Claude remembers new facts)
```

Company knowledge is served by a small **MCP server** (`mcp_server.py`) that
`chatbot.py` launches automatically — Claude calls its `search_company_docs`
tool when it needs the docs. Because it's a standard MCP server over stdio, you
can also connect other MCP clients (e.g. Claude Desktop) to it.

## 1. One-time setup

### a) Activate your virtual environment
```powershell
venv\Scripts\activate
```

### b) Install dependencies
```powershell
pip install -r requirements.txt
```

### c) Add your secrets
Open `.env` and fill in:
- `ANTHROPIC_API_KEY` — from https://console.anthropic.com/ → API Keys. **Required.**
- `ANTHROPIC_MODEL` — defaults to `claude-opus-4-8`. Set to `claude-haiku-4-5`
  (cheapest/fastest) or `claude-sonnet-4-6` to lower cost.
- `COMPANY_NAME` — your company's name (used in the bot's persona).

The embedding model runs **locally and free** — no HuggingFace token needed.

### d) Start PostgreSQL
Per-user profiles + message history live in Postgres. The easiest way is Docker
(needs Docker Desktop running):
```powershell
docker compose up -d
```
This starts Postgres on `localhost:5432` with credentials matching the default
`DATABASE_URL` in `.env`. (Already have a Postgres? Just point `DATABASE_URL`
at it — the bot creates its tables automatically on first run.)

## 2. Build the company knowledge base
Put your PDFs in the `docs/` folder, then run:
```powershell
python ingest.py
```
This creates a `vector_db/` folder. Re-run it whenever you add/change documents.

## 3. Chat in the terminal (do this first!)
```powershell
python test_chat.py
```
You "log in" with a phone number, then chat. Tell it your name, ask about your
company, quit, re-run with the same number — it still remembers you. Use a
different number to confirm each user gets separate memory.

## 4. Connect to WhatsApp
Both webhook servers call the same Claude brain — `chatbot.answer(phone, message)`
— passing the sender's WhatsApp number as the memory key, so each contact gets
their own profile + history automatically. Pick **one** backend (only one can
use port 5000 at a time).

### Run the server (Twilio)
```powershell
python main.py
```
The server starts on http://localhost:5000. (For production, see
[Run in production](#run-in-production) below.)

### Expose it to the internet with ngrok
WhatsApp needs a public URL to reach your local server.
1. Download ngrok: https://ngrok.com/download
2. In a **second** terminal:
   ```powershell
   ngrok http 5000
   ```
3. Copy the `https://....ngrok-free.app` URL it shows.

### Connect the Twilio WhatsApp sandbox
1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**.
2. From your phone, send the join code (e.g. `join <two-words>`) to the
   Twilio sandbox number to activate WhatsApp.
3. In the sandbox settings, set **"When a message comes in"** to:
   ```
   https://<your-ngrok-url>/whatsapp
   ```
   Method: **POST**. Save.
4. **Chat!** Message the sandbox number — replies are grounded in your PDFs and
   the bot remembers what you tell it.

---

## Alternative: Meta WhatsApp Cloud API (`main_meta.py`)
Use this instead of Twilio to run on your own number or Meta's free test number.

1. **Create a Meta app**: https://developers.facebook.com/apps → type **Business**
   → Add Product → **WhatsApp** → Set up.
2. **WhatsApp → API Setup** page gives you:
   - a **Temporary access token** → `META_ACCESS_TOKEN` in `.env`
   - a **Phone number ID** → `META_PHONE_NUMBER_ID` in `.env`
   - a free **test number** to send from
   - **Add a recipient** (your personal WhatsApp) — Meta only delivers to numbers
     you add here while in test mode.
   - ⚠️ The temporary token expires in 24h; create a System User token for a
     permanent one.
3. **Pick a verify token**: any secret string in `META_VERIFY_TOKEN` (e.g.
   `my_secret_verify_123`). You'll type the same value into Meta.
4. **Run the server**:
   ```powershell
   python main_meta.py
   ```
   and in a second terminal: `ngrok http 5000`
5. **Configure webhook** (WhatsApp → Configuration → Edit):
   - Callback URL: `https://<your-ngrok-url>/whatsapp`
   - Verify token: same as `META_VERIFY_TOKEN`
   - Click **Verify and save**, then **Subscribe** to the **messages** field.
6. **Chat**: from your added recipient number, message the test number.

---

## Run in production
The `python main.py` command uses Flask's built-in **development** server, which
isn't meant for production. Use the bundled **waitress** WSGI server instead:

```powershell
# Twilio backend
waitress-serve --listen=0.0.0.0:5000 main:app

# Meta backend
waitress-serve --listen=0.0.0.0:5000 main_meta:app
```

Production checklist:
- Put a real HTTPS endpoint in front (a domain + reverse proxy, or a host like
  Render/Railway/Fly) instead of ngrok, and point the WhatsApp webhook at it.
- Use a **permanent** Meta access token (System User token), not the 24h one.
- Keep `ANTHROPIC_API_KEY` and other secrets in the host's secret manager / env
  vars — never commit `.env`.
- Point `DATABASE_URL` at a **managed Postgres** (RDS, Cloud SQL, Neon, Supabase,
  …) rather than the local Docker one. It holds personal data — enable backups,
  restrict network access, and use a strong password (not `postgres/postgres`).

---

## Files
| File | Purpose |
|------|---------|
| `chatbot.py` | The Claude brain: tool-use loop over company RAG + per-user memory + history |
| `mcp_server.py` | Standalone MCP server (stdio) exposing `search_company_docs` over the FAISS DB |
| `mcp_client.py` | Sync↔async bridge that lets `chatbot.py` call the MCP server |
| `store.py` | PostgreSQL layer (per-user profile + message history, keyed by phone) |
| `ingest.py` | Builds the FAISS vector DB from the PDFs in `docs/` |
| `main.py` | Twilio WhatsApp webhook |
| `main_meta.py` | Meta WhatsApp Cloud API webhook |
| `test_chat.py` | Chat with the bot in the terminal (no WhatsApp) |
| `docker-compose.yml` | Local PostgreSQL for the per-user store |
| `docs/` | Your PDF documents |
| `vector_db/` | Generated vector database (do not edit) |
| `.env` | Your secrets (never commit this) |

## Configuration (in `.env`)
- `ANTHROPIC_API_KEY` — your Claude API key. **Required.**
- `ANTHROPIC_MODEL` — model id (default `claude-opus-4-8`).
- `COMPANY_NAME` — shown in the bot's persona.
- `DATABASE_URL` — Postgres connection (default `postgresql://postgres:postgres@localhost:5432/whatsapp_bot`).
- `HF_EMBEDDING_MODEL` — local embedding model (default `sentence-transformers/all-MiniLM-L6-v2`).
- Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`.
- Meta: `META_VERIFY_TOKEN`, `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_GRAPH_API_VERSION`.

## Troubleshooting
- **`vector_db/ not found`** → run `python ingest.py` first (the MCP server loads it on startup).
- **Bot starts but company answers are empty / "Connecting to company-docs MCP server" hangs**
  → the MCP server subprocess failed to start. Run `python mcp_server.py` directly to
  see its error (usually a missing `vector_db/` or a dependency install issue).
- **`ANTHROPIC_API_KEY is missing`** → paste your key into `.env`.
- **`connection refused` / can't reach Postgres** → start it with `docker compose up -d`
  (Docker Desktop must be running), or fix `DATABASE_URL`. Use the plain
  `postgresql://…` form, **not** `postgresql+psycopg://…`.
- **No reply on WhatsApp (Twilio)** → confirm the ngrok URL is set in Twilio and ends with `/whatsapp`.
- **Meta webhook "verify failed"** → the verify token in Meta must match `META_VERIFY_TOKEN` exactly.
- **Meta: message received but no reply** → token expired (regenerate), recipient
  number not added in API Setup, or you didn't **Subscribe** to the `messages` webhook field.
- **Port 5000 in use** → only one server (`main.py` OR `main_meta.py`) can run at a time.
- **Bot doesn't remember a user** → memory is keyed by phone number; confirm the
  same number is being used (numbers are normalized, so `+91...` and `whatsapp:+91...` match).

# WhatsApp Document Chatbot

A WhatsApp chatbot that answers questions based on **your own PDF documents**,
built with **LangChain**, **free HuggingFace** models/embeddings, and a local
**FAISS** vector database.

Two WhatsApp backends are included (pick one):
- **`main.py`** — Twilio WhatsApp sandbox (fastest to test)
- **`main_meta.py`** — Meta WhatsApp Cloud API (your own / Meta test number)

Both share the same brain (`rag.py`).

```
Your PDFs (docs/) ──> ingest.py ──> embeddings (HuggingFace) ──> FAISS vector_db/
                                                                       │
WhatsApp msg ──> Twilio / Meta ──> webhook ──> retrieve chunks ──> HF LLM ──> reply
```

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
- `HUGGINGFACEHUB_API_TOKEN` — free token from https://huggingface.co/settings/tokens (type: Read).
  The LLM (`Qwen/Qwen2.5-7B-Instruct`) runs on HuggingFace's servers — **not** your PC.
- `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` — from https://console.twilio.com

## 2. Build the vector database
Put your PDFs in the `docs/` folder, then run:
```powershell
python ingest.py
```
This creates a `vector_db/` folder. Re-run it whenever you add/change documents.

## 3. Run the chatbot server
```powershell
python main.py
```
The server starts on http://localhost:5000

## 4. Expose it to the internet with ngrok
Twilio needs a public URL to reach your local server.
1. Download ngrok: https://ngrok.com/download
2. In a **second** terminal:
   ```powershell
   ngrok http 5000
   ```
3. Copy the `https://....ngrok-free.app` URL it shows.

## 5. Connect Twilio WhatsApp sandbox
1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message**.
2. From your phone, send the join code (e.g. `join <two-words>`) to the
   Twilio sandbox number to activate WhatsApp.
3. In the sandbox settings, set **"When a message comes in"** to:
   ```
   https://<your-ngrok-url>/whatsapp
   ```
   Method: **POST**. Save.

## 6. Chat!
Send a WhatsApp message with a question about your documents to the sandbox
number. The bot replies with an answer grounded in your PDFs.

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

## Multi-user version (Postgres + pgvector)
Serve many users, each with their OWN documents. The bot identifies the sender
by WhatsApp number and answers using only that user's data.

**Architecture:** every document chunk is tagged with a `user_id` and stored in
**pgvector** (inside Postgres). Retrieval is filtered by `user_id`, so users
never see each other's data.

### Setup
1. **Start Postgres + pgvector** (needs Docker Desktop running):
   ```powershell
   docker compose up -d
   ```
   This launches Postgres on `localhost:5432` with the connection string already
   set in `.env` as `DATABASE_URL`.

2. **Register a user and ingest their PDFs** (creates the user + the tables):
   ```powershell
   python ingest_user.py --number +9170771xxxxx --name "Acme Corp" --docs docs
   ```
   Repeat for each user with their own `--number` and `--docs` folder.

3. **Run the multi-user webhook** (instead of `main_meta.py`):
   ```powershell
   python main_meta_multi.py
   ```
   Then connect ngrok + the Meta webhook exactly as in the Meta section above.

4. **Chat**: each registered number gets answers from only their own documents.
   Unregistered numbers get a "you're not set up yet" message.

### Multi-user files
| File | Purpose |
|------|---------|
| `docker-compose.yml` | Postgres + pgvector database |
| `db.py` | DB connection + tables (users, documents, messages) |
| `rag_multi.py` | Per-user RAG (pgvector, filtered by `user_id`) |
| `ingest_user.py` | Register a user + ingest their PDFs |
| `main_meta_multi.py` | Multi-user Meta webhook |

---

### Files
| File | Purpose |
|------|---------|
| `docs/` | Your PDF documents |
| `ingest.py` | Builds the FAISS vector DB from the PDFs |
| `rag.py` | Shared RAG brain (vector DB + LLM) used by both servers |
| `main.py` | Twilio WhatsApp webhook |
| `main_meta.py` | Meta WhatsApp Cloud API webhook |
| `test_chat.py` | Test the bot in the terminal (no WhatsApp) |
| `vector_db/` | Generated vector database (do not edit) |
| `.env` | Your secrets (never commit this) |

### Configuration (in `.env`)
- `HF_LLM_REPO_ID` — which HuggingFace LLM to use (default: `Qwen/Qwen2.5-7B-Instruct`)
- `HF_EMBEDDING_MODEL` — embedding model (default: `sentence-transformers/all-MiniLM-L6-v2`)

### Troubleshooting
- **`vector_db/ not found`** → run `python ingest.py` first.
- **HuggingFace 401/403** → check your token in `.env`.
- **No reply on WhatsApp (Twilio)** → confirm ngrok URL is set in Twilio and ends with `/whatsapp`.
- **Meta webhook "verify failed"** → the verify token in Meta must match `META_VERIFY_TOKEN` exactly.
- **Meta: message received but no reply** → token expired (regenerate) or recipient
  number not added in API Setup, or you didn't **Subscribe** to the `messages` webhook field.
- **Port 5000 in use** → only one server (`main.py` OR `main_meta.py`) can run at a time.
- **Multi-user: connection refused / port 5432** → Docker Desktop isn't running, or `docker compose up -d` wasn't run.
- **Multi-user: bot says "not set up yet"** → that number isn't registered; run `ingest_user.py` for it.

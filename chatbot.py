"""
chatbot.py
==========
The brain of the bot, powered by **Claude** (Anthropic API).

What it does on every incoming message from a phone number:

  1. Loads that user's recent conversation history + saved personal facts
     from SQLite (see store.py). The phone number is the key.
  2. Retrieves the most relevant chunks from the company documents
     (local FAISS vector DB built by ingest.py — RAG).
  3. Asks Claude to answer, giving it the company context + what we already
     know about this user + the conversation so far.
  4. If the user shares personal info, Claude calls the `save_personal_info`
     tool and we persist it — so next time we can answer from memory.
  5. Logs both the user's message and the bot's reply to the history.

Embeddings stay local & free (sentence-transformers); only the LLM is Claude.

Public API:
    answer(phone, message) -> str
"""

import os

import anthropic
from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

import store

load_dotenv()

# --- Configuration ---------------------------------------------------------
# Default to the most capable model. Set ANTHROPIC_MODEL=claude-haiku-4-5 (or
# claude-sonnet-4-6) in .env for a cheaper/faster bot once you're happy.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
VECTOR_DB_DIR = "vector_db"
TOP_K = 4          # how many document chunks to retrieve per question
MAX_TOKENS = 1024  # WhatsApp replies are short; keep responses bounded

# The company name shown to users. Override with COMPANY_NAME in .env.
COMPANY_NAME = os.getenv("COMPANY_NAME", "the company")

# Stable instructions — kept byte-for-byte identical across requests so the
# prompt-caching prefix stays valid (see Anthropic prompt caching).
SYSTEM_INSTRUCTIONS = f"""You are a friendly, concise WhatsApp assistant for {COMPANY_NAME}.

How to behave:
- For questions about {COMPANY_NAME} or its products/services, answer ONLY from
  the "Company knowledge" section provided to you. If the answer isn't there,
  say you don't have that information yet — do not make things up.
- The user may tell you personal details about themselves (their name, email,
  role, company, location, preferences, etc.). Whenever they share such a fact,
  call the `save_personal_info` tool so it is remembered for next time.
- Use the "What you already know about this user" section to personalize your
  replies and to recall things they told you earlier (e.g. their name).
- Keep replies short, clear and warm — this is a WhatsApp chat, not an essay.
- Never reveal another user's information; you only ever know about the person
  you are currently talking to."""

# Tool Claude calls to persist personal facts about the current user.
SAVE_TOOL = {
    "name": "save_personal_info",
    "description": (
        "Save or update personal information the user shares about THEMSELVES "
        "(e.g. name, email, company, role, location, preferences). Call this "
        "whenever the user states a fact about themselves, so it can be "
        "remembered and used in future conversations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "object",
                "description": (
                    "Key-value pairs of personal facts. Use short snake_case "
                    'keys, e.g. {"name": "Aditya", "city": "Bhubaneswar", '
                    '"role": "developer"}.'
                ),
                "additionalProperties": {"type": "string"},
            }
        },
        "required": ["facts"],
    },
}


# --- Load expensive objects ONCE (shared across all requests) --------------
def _load_retriever():
    if not os.path.isdir(VECTOR_DB_DIR):
        raise SystemExit(
            f"[ERROR] '{VECTOR_DB_DIR}/' not found. Run `python ingest.py` first "
            "to build the company knowledge base from your PDFs."
        )
    print("Loading embedding model + company knowledge base ...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    vector_db = FAISS.load_local(
        VECTOR_DB_DIR, embeddings, allow_dangerous_deserialization=True
    )
    return vector_db.as_retriever(search_kwargs={"k": TOP_K})


_retriever = _load_retriever()

# anthropic.Anthropic() reads ANTHROPIC_API_KEY from the environment.
if not os.getenv("ANTHROPIC_API_KEY"):
    raise SystemExit(
        "[ERROR] ANTHROPIC_API_KEY is missing in your .env file.\n"
        "        Get one from https://console.anthropic.com/ -> API Keys."
    )
_client = anthropic.Anthropic()
print(f"[OK] Chatbot ready (model: {MODEL}).")


def _retrieve_company_context(question: str) -> str:
    """Return the most relevant company-document chunks as one text block."""
    docs = _retriever.invoke(question)
    if not docs:
        return "(no relevant company documents found)"
    return "\n\n---\n\n".join(doc.page_content for doc in docs)


def _profile_to_text(profile: dict) -> str:
    if not profile:
        return "(nothing known yet — this may be a new user)"
    return "\n".join(f"- {key}: {value}" for key, value in profile.items())


def _build_system(phone: str, question: str):
    """Build the system prompt: a cached stable block + a dynamic context block."""
    company_context = _retrieve_company_context(question)
    profile = store.get_profile(phone)
    dynamic = (
        "Company knowledge (use this to answer questions about "
        f"{COMPANY_NAME}):\n{company_context}\n\n"
        f"What you already know about this user (phone {phone}):\n"
        f"{_profile_to_text(profile)}"
    )
    return [
        # Stable prefix — cached to cut cost/latency on repeat calls.
        {
            "type": "text",
            "text": SYSTEM_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        },
        # Volatile per-request context goes AFTER the cache breakpoint.
        {"type": "text", "text": dynamic},
    ]


def answer(phone: str, message: str) -> str:
    """Answer one WhatsApp message from `phone`, with memory + company RAG."""
    message = (message or "").strip()
    if not message:
        return "Send me a question and I'll help! 🙂"

    store.get_or_create_user(phone)

    # History from BEFORE this message, then append the new turn.
    history = store.get_recent_messages(phone)
    messages = history + [{"role": "user", "content": message}]
    store.log_message(phone, "user", message)

    system = _build_system(phone, message)

    try:
        # Manual tool-use loop: keep going until Claude stops calling tools.
        while True:
            response = _client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[SAVE_TOOL],
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                break

            # Persist any personal facts Claude decided to save, then continue.
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "save_personal_info":
                    facts = (block.input or {}).get("facts", {})
                    store.update_profile(phone, facts)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Saved.",
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        reply = "".join(b.text for b in response.content if b.type == "text").strip()
    except anthropic.APIError as exc:
        print(f"[ERROR] Anthropic API: {exc}")
        return "Sorry, something went wrong while answering. Please try again."

    if not reply:
        reply = "I'm not sure how to answer that — could you rephrase?"

    store.log_message(phone, "assistant", reply)
    return reply[:1500]  # WhatsApp-safe length

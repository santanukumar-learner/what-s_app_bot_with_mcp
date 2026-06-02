"""
chatbot.py
==========
The brain of the bot, powered by **Claude** (Anthropic API).

What it does on every incoming message from a phone number:

  1. Loads that user's recent conversation history + saved personal facts
     from Postgres (see store.py). The phone number is the key.
  2. Asks Claude to answer, giving it what we already know about this user +
     the conversation so far, plus two tools it can call.
  3. Company knowledge is reached through an **MCP server** (mcp_server.py, via
     mcp_client.py): Claude calls the `search_company_docs` tool whenever it
     needs to look something up in the company documents (RAG).
  4. If the user shares personal info, Claude calls the `save_personal_info`
     tool and we persist it — so next time we can answer from memory.
  5. Logs both the user's message and the bot's reply to the history.

Retrieval/embeddings stay local & free (sentence-transformers, behind the MCP
server); only the LLM is Claude.

Public API:
    answer(phone, message) -> str
"""

import os

import anthropic
import psycopg
from dotenv import load_dotenv
from psycopg_pool import PoolTimeout

import mcp_client  # bridge to the company-docs MCP server (mcp_server.py)
import store

# Raised by the Postgres store/pool when the DB is unreachable or slow. We catch
# these to fail gracefully ("systems are updating") instead of crashing a reply.
_DB_ERRORS = (psycopg.Error, PoolTimeout)
_DB_BUSY_MSG = (
    "Our systems are updating right now — please send that again in a moment. 🙏"
)

load_dotenv()

# --- Configuration ---------------------------------------------------------
# Default to the most capable model. Set ANTHROPIC_MODEL=claude-haiku-4-5 (or
# claude-sonnet-4-6) in .env for a cheaper/faster bot once you're happy.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
MAX_TOKENS = 1024  # WhatsApp replies are short; keep responses bounded

# The company name shown to users. Override with COMPANY_NAME in .env.
COMPANY_NAME = os.getenv("COMPANY_NAME", "the company")

# Stable instructions — kept byte-for-byte identical across requests so the
# prompt-caching prefix stays valid (see Anthropic prompt caching).
SYSTEM_INSTRUCTIONS = f"""You are a friendly, concise WhatsApp assistant for {COMPANY_NAME}.

How to behave:
- GREETING: Check the "What you already know about this user" section. If it
  already has their name, greet the returning user BY NAME and, when relevant,
  refer back to what they told you earlier. If you know little or nothing about
  them, treat them as new: warmly welcome them and — early in the conversation —
  ask their name (and at most one other detail that helps you serve them). Keep
  this a light "discovery" step, never an interrogation.
- COMPANY QUESTIONS: For ANY question about {COMPANY_NAME} or its
  products/services/policies, call the `search_company_docs` tool first and
  answer ONLY from what it returns. You may search more than once with different
  wording. If the documents don't cover it, say you don't have that information
  yet — do not make things up.
- REMEMBERING: Whenever the user shares a personal detail about THEMSELVES
  (name, email, role, company, location, preferences, etc.), call the
  `save_personal_info` tool so it is remembered next time. If the tool reports a
  value was invalid (e.g. a malformed email or phone number), apologize briefly
  and ask them to send it again correctly — do not claim it was saved.
- PRIVACY: Never reveal another user's information; you only ever know about the
  person you are currently talking to.

WhatsApp formatting (keep replies native and skimmable):
- Use *single asterisks* for bold (WhatsApp style) — never **double asterisks**.
- Offer choices as a numbered list (1, 2, 3) so the user can reply with a number.
- Keep every reply under ~150 words so WhatsApp doesn't truncate it; only split
  into multiple short messages if absolutely necessary."""

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
# anthropic.Anthropic() reads ANTHROPIC_API_KEY from the environment.
if not os.getenv("ANTHROPIC_API_KEY"):
    raise SystemExit(
        "[ERROR] ANTHROPIC_API_KEY is missing in your .env file.\n"
        "        Get one from https://console.anthropic.com/ -> API Keys."
    )
_client = anthropic.Anthropic()

# Company-docs retrieval lives behind the MCP server (mcp_server.py). Fetch its
# tool definitions once; Claude calls them inside the tool-use loop below. This
# also starts the server subprocess + loads the embedding model.
print("Connecting to company-docs MCP server ...")
MCP_TOOLS = mcp_client.get_tools()
TOOLS = [SAVE_TOOL, *MCP_TOOLS]
print(f"[OK] Chatbot ready (model: {MODEL}; MCP tools: "
      f"{', '.join(t['name'] for t in MCP_TOOLS) or 'none'}).")


def _profile_to_text(profile: dict) -> str:
    if not profile:
        return "(nothing known yet — this may be a new user)"
    return "\n".join(f"- {key}: {value}" for key, value in profile.items())


def _build_system(phone: str):
    """Build the system prompt: a cached stable block + a dynamic context block."""
    profile = store.get_profile(phone)
    dynamic = (
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

    # Load the user + history. A DB hiccup here must not crash the reply —
    # fail gracefully ("systems are updating") per the production spec.
    try:
        store.get_or_create_user(phone)
        history = store.get_recent_messages(phone)
        store.log_message(phone, "user", message)
        system = _build_system(phone)
    except _DB_ERRORS as exc:
        print(f"[ERROR] DB (load): {exc}")
        return _DB_BUSY_MSG

    # History from BEFORE this message, then append the new turn.
    messages = history + [{"role": "user", "content": message}]

    try:
        # Manual tool-use loop: keep going until Claude stops calling tools.
        while True:
            response = _client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                break

            # Run every tool Claude called this turn, then feed results back.
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "save_personal_info":
                    result = _handle_save(phone, block.input or {})
                else:
                    # Company-docs (and any future) tools live in the MCP server.
                    result = mcp_client.call_tool(block.name, block.input or {})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        reply = "".join(b.text for b in response.content if b.type == "text").strip()
    except anthropic.APIError as exc:
        print(f"[ERROR] Anthropic API: {exc}")
        return "Sorry, something went wrong while answering. Please try again."

    if not reply:
        reply = "I'm not sure how to answer that — could you rephrase?"

    # Logging the reply is best-effort: a DB blip here shouldn't drop the answer.
    try:
        store.log_message(phone, "assistant", reply)
    except _DB_ERRORS as exc:
        print(f"[WARN] DB (log reply): {exc}")

    return reply[:1500]  # WhatsApp-safe length


def _handle_save(phone: str, tool_input: dict) -> str:
    """Validate then persist personal facts; return a tool_result string.

    Validation happens BEFORE the DB write (production spec): invalid email /
    phone values are rejected with a message Claude can relay so the user can
    correct them. DB errors fail gracefully instead of crashing the turn.
    """
    facts = (tool_input or {}).get("facts", {})
    errors = store.validate_personal_facts(facts)
    if errors:
        return "Not saved — " + "; ".join(errors) + ". Please ask the user to correct it."
    try:
        store.update_profile(phone, facts)
    except _DB_ERRORS as exc:
        print(f"[ERROR] DB (save): {exc}")
        return "Could not save that right now — our systems are updating."
    return "Saved."

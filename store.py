import os
import re

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

load_dotenv()

# --- Field validation (run BEFORE any profile write) -----------------------
# Simple, dependency-free checks. The equivalents in the Aayiq platform live in
# `@aayiq/shared/validators` (parseEmail / parsePhoneE164) — keep this behaviour
# when the bot is merged there.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")  # E.164-ish (8–15 digits)

# Profile keys we treat as an email / a phone number for validation purposes.
_EMAIL_KEYS = {"email", "email_address", "mail"}
_PHONE_KEYS = {"phone", "phone_number", "mobile", "mobile_number", "contact", "whatsapp"}


def validate_personal_facts(facts: dict) -> list[str]:
    """Return a list of human-readable errors for invalid email/phone values.

    Empty list == everything is valid. Other keys are accepted as-is.
    """
    errors: list[str] = []
    for key, value in (facts or {}).items():
        name = str(key).lower()
        val = str(value).strip()
        if name in _EMAIL_KEYS and not _EMAIL_RE.match(val):
            errors.append(f"'{value}' is not a valid email address")
        elif name in _PHONE_KEYS and not _PHONE_RE.match(
            val.replace(" ", "").replace("-", "")
        ):
            errors.append(f"'{value}' is not a valid phone number")
    return errors

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/whatsapp_bot",
)

HISTORY_LIMIT = 12  

_pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row},
    open=False,
)
_pool.open()


def normalize_number(number: str) -> str:
    """Strip 'whatsapp:' prefixes, spaces, and a leading '+' for consistency."""
    return number.replace("whatsapp:", "").replace(" ", "").lstrip("+").strip()


def init_db() -> None:
    """Create tables/indexes if they don't exist (idempotent). Run at import."""
    with _pool.connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                phone      TEXT PRIMARY KEY,
                name       TEXT        NOT NULL DEFAULT '',
                profile    JSONB       NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         BIGSERIAL   PRIMARY KEY,
                phone      TEXT        NOT NULL REFERENCES users(phone),
                role       TEXT        NOT NULL,   -- 'user' or 'assistant'
                text       TEXT        NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone)"
        )


def get_or_create_user(phone: str) -> dict:
    """Return the user row (as a dict) for this phone, creating it if needed."""
    phone = normalize_number(phone)
    with _pool.connection() as conn:
        conn.execute(
            "INSERT INTO users (phone) VALUES (%s) ON CONFLICT (phone) DO NOTHING",
            (phone,),
        )
        return conn.execute(
            "SELECT phone, name, profile, created_at FROM users WHERE phone = %s",
            (phone,),
        ).fetchone()


def get_profile(phone: str) -> dict:
    """Return the stored dict of personal facts for this phone (empty if none)."""
    phone = normalize_number(phone)
    with _pool.connection() as conn:
        row = conn.execute(
            "SELECT profile FROM users WHERE phone = %s", (phone,)
        ).fetchone()
    return row["profile"] if row and row["profile"] else {}


def update_profile(phone: str, updates: dict) -> dict:
    """Merge `updates` into the user's stored profile and return the merged dict.

    New keys are added; existing keys are overwritten with the newer value.
    """
    phone = normalize_number(phone)
    profile = get_profile(phone)
    # Keep everything as strings for a simple, predictable store.
    profile.update({str(k): str(v) for k, v in (updates or {}).items()})

    # If the user told us their name, also mirror it onto the users.name column.
    name = profile.get("name", "")

    with _pool.connection() as conn:
        conn.execute(
            "UPDATE users SET profile = %s, name = %s WHERE phone = %s",
            (Json(profile), name, phone),
        )
    return profile


def log_message(phone: str, role: str, text: str) -> None:
    """Save one message (role = 'user' or 'assistant') to the history."""
    phone = normalize_number(phone)
    with _pool.connection() as conn:
        conn.execute(
            "INSERT INTO messages (phone, role, text) VALUES (%s, %s, %s)",
            (phone, role, text),
        )


def get_recent_messages(phone: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Return the most recent messages for this phone, oldest-first.

    Shape: [{"role": "user"|"assistant", "content": "..."}, ...] — ready to
    drop straight into the Anthropic `messages` array.
    """
    phone = normalize_number(phone)
    with _pool.connection() as conn:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE phone = %s "
            "ORDER BY id DESC LIMIT %s",
            (phone, limit),
        ).fetchall()
    # rows come back newest-first; reverse so the conversation reads in order.
    return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]


# Create the tables as soon as this module is imported.
init_db()

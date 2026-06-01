import json
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "chatbot.db")

HISTORY_LIMIT = 12  # how many recent messages to feed back to Claude


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_number(number: str) -> str:
    """Strip 'whatsapp:' prefixes, spaces, and a leading '+' for consistency."""
    return number.replace("whatsapp:", "").replace(" ", "").lstrip("+").strip()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist (idempotent). Called at import time."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                phone      TEXT PRIMARY KEY,
                name       TEXT NOT NULL DEFAULT '',
                profile    TEXT NOT NULL DEFAULT '{}',   -- JSON of personal facts
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                phone      TEXT NOT NULL,
                role       TEXT NOT NULL,                 -- 'user' or 'assistant'
                text       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (phone) REFERENCES users(phone)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone);
            """
        )


def get_or_create_user(phone: str) -> dict:
    """Return the user row (as a dict) for this phone, creating it if needed."""
    phone = normalize_number(phone)
    with _connect() as conn:
        row = conn.execute(
            "SELECT phone, name, profile, created_at FROM users WHERE phone = ?",
            (phone,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (phone, name, profile, created_at) VALUES (?, '', '{}', ?)",
                (phone, _now()),
            )
            return {"phone": phone, "name": "", "profile": {}, "created_at": _now()}
        return {
            "phone": row["phone"],
            "name": row["name"],
            "profile": json.loads(row["profile"] or "{}"),
            "created_at": row["created_at"],
        }


def get_profile(phone: str) -> dict:
    """Return the stored dict of personal facts for this phone (empty if none)."""
    phone = normalize_number(phone)
    with _connect() as conn:
        row = conn.execute(
            "SELECT profile FROM users WHERE phone = ?", (phone,)
        ).fetchone()
    return json.loads(row["profile"]) if row and row["profile"] else {}


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

    with _connect() as conn:
        conn.execute(
            "UPDATE users SET profile = ?, name = ? WHERE phone = ?",
            (json.dumps(profile), name, phone),
        )
    return profile


def log_message(phone: str, role: str, text: str) -> None:
    """Save one message (role = 'user' or 'assistant') to the history."""
    phone = normalize_number(phone)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (phone, role, text, created_at) VALUES (?, ?, ?, ?)",
            (phone, role, text, _now()),
        )


def get_recent_messages(phone: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Return the most recent messages for this phone, oldest-first.

    Shape: [{"role": "user"|"assistant", "content": "..."}, ...] — ready to
    drop straight into the Anthropic `messages` array.
    """
    phone = normalize_number(phone)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE phone = ? "
            "ORDER BY id DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
    # rows come back newest-first; reverse so the conversation reads in order.
    return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]


# Create the tables as soon as this module is imported.
init_db()

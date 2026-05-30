"""
main_meta_multi.py
==================
MULTI-USER WhatsApp bot (Meta Cloud API).

Unlike main_meta.py (one shared knowledge base), this version:
  * identifies the sender by their WhatsApp number,
  * looks them up in the database,
  * answers using ONLY that user's documents (pgvector filtered by user_id),
  * logs the conversation history.

Run:
    python main_meta_multi.py
(Requires Postgres running + at least one user ingested via ingest_user.py.)
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, request

from db import get_user_by_number, init_db, log_message
from rag_multi import answer_for_user

load_dotenv()

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v21.0")

# Message sent to a number that isn't registered with any documents yet.
UNKNOWN_USER_REPLY = (
    "Hi! You're not set up yet. Please ask the administrator to register your "
    "number and upload your documents."
)

app = Flask(__name__)
init_db()  # make sure tables exist on startup


def send_whatsapp_message(to: str, text: str) -> None:
    """Send a text reply back to the user via the Meta Graph API."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code >= 400:
        print(f"[SEND ERROR] {resp.status_code}: {resp.text}")
    else:
        print(f"[SENT] to {to}")


@app.route("/whatsapp", methods=["GET"])
def verify_webhook():
    """Meta calls this once to verify the webhook (handshake)."""
    if (
        request.args.get("hub.mode") == "subscribe"
        and request.args.get("hub.verify_token") == VERIFY_TOKEN
    ):
        print("[WEBHOOK] verified OK")
        return request.args.get("hub.challenge"), 200
    print("[WEBHOOK] verification failed")
    return "Verification failed", 403


@app.route("/whatsapp", methods=["POST"])
def receive_message():
    """Meta sends incoming WhatsApp messages here as JSON."""
    data = request.get_json(silent=True) or {}
    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages")
        if not messages:
            return "OK", 200  # status update, ignore

        message = messages[0]
        sender = message["from"]
        text = message.get("text", {}).get("body", "").strip()
        print(f"\n[MSG] from {sender}: {text!r}")

        # --- The multi-user part: who is this sender? ---
        user = get_user_by_number(sender)
        if user is None:
            print(f"[INFO] {sender} is not a registered user.")
            send_whatsapp_message(sender, UNKNOWN_USER_REPLY)
            return "OK", 200

        if not text:
            send_whatsapp_message(sender, "Send me a question about your documents!")
            return "OK", 200

        log_message(user.id, "user", text)
        reply = answer_for_user(user.id, text)
        log_message(user.id, "bot", reply)
        print(f"[REPLY] {reply!r}")
        send_whatsapp_message(sender, reply)
    except (KeyError, IndexError) as exc:
        print(f"[PARSE ERROR] {exc} -- payload: {data}")

    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "Multi-user WhatsApp bot is running. Webhook at /whatsapp.", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

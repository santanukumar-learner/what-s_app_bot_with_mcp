"""
main_meta.py
============
WhatsApp chatbot using the **Meta WhatsApp Cloud API** (your own number /
Meta test number) instead of the Twilio sandbox.

It answers questions from YOUR documents using the shared RAG logic in rag.py.

How Meta's webhook works (different from Twilio):
  * Meta first sends a GET request to verify your webhook (handshake).
  * Then Meta sends incoming messages as POST requests with nested JSON.
  * You reply by calling the Meta Graph API with your access token
    (the reply is NOT inline like Twilio's TwiML).

Required .env values:
  META_VERIFY_TOKEN    - any secret string you choose (used in the webhook setup)
  META_ACCESS_TOKEN    - access token from your Meta app (WhatsApp > API Setup)
  META_PHONE_NUMBER_ID - the "Phone number ID" from WhatsApp > API Setup

Run:
    python main_meta.py
Then `ngrok http 5000` and register https://<ngrok-url>/whatsapp as the
Callback URL in your Meta app (with the same verify token).
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, request

from rag import answer_question  # shared RAG logic (vector DB + LLM)

load_dotenv()

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v21.0")

app = Flask(__name__)


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
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[WEBHOOK] verified OK")
        return challenge, 200
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
            # Could be a status update (delivered/read) — ignore those.
            return "OK", 200

        message = messages[0]
        sender = message["from"]                 # user's WhatsApp number
        text = message.get("text", {}).get("body", "").strip()
        print(f"\n[MSG] from {sender}: {text!r}")

        reply = answer_question(text) if text else (
            "Send me a question about the documents and I'll help!"
        )
        print(f"[REPLY] {reply!r}")
        send_whatsapp_message(sender, reply)
    except (KeyError, IndexError) as exc:
        print(f"[PARSE ERROR] {exc} -- payload: {data}")

    # Always 200 quickly so Meta doesn't retry.
    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "Meta WhatsApp document chatbot is running. Webhook at /whatsapp.", 200


if __name__ == "__main__":
    missing = [
        name
        for name, val in {
            "META_VERIFY_TOKEN": VERIFY_TOKEN,
            "META_ACCESS_TOKEN": ACCESS_TOKEN,
            "META_PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        }.items()
        if not val
    ]
    if missing:
        print(f"[WARNING] Missing .env values: {', '.join(missing)}")
    app.run(host="0.0.0.0", port=5000, debug=False)

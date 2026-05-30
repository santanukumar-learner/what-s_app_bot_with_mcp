

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from rag import answer_question  # shared RAG logic (vector DB + LLM)

app = Flask(__name__)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Twilio calls this endpoint with each incoming WhatsApp message."""
    incoming_msg = request.values.get("Body", "").strip()
    sender = request.values.get("From", "unknown")
    print(f"\n[MSG] from {sender}: {incoming_msg!r}")

    reply_text = answer_question(incoming_msg) if incoming_msg else (
        "Send me a question about the documents and I'll help!"
    )
    print(f"[REPLY] {reply_text!r}")

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return str(twiml)


@app.route("/", methods=["GET"])
def health():
    """Simple health check so you can confirm the server is up in a browser."""
    return "WhatsApp document chatbot is running. POST to /whatsapp.", 200


if __name__ == "__main__":
    # host=0.0.0.0 so ngrok / external tunnels can reach it.
    app.run(host="0.0.0.0", port=5000, debug=False)

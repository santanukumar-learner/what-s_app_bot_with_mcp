"""
test_chat.py
============
Quick command-line test of the RAG pipeline WITHOUT WhatsApp/Twilio.
Use it to confirm your HuggingFace token + LLM + vector DB all work.

Requires: HUGGINGFACEHUB_API_TOKEN set in .env, and `python ingest.py`
already run.

Usage:
    python test_chat.py
Then type questions about your documents. Type 'quit' to exit.
"""

from rag import answer_question  # reuses the same RAG chain as the WhatsApp bot


def main():
    print("=" * 60)
    print("Document chatbot — local test (no WhatsApp).")
    print("Ask a question about your PDFs. Type 'quit' to exit.")
    print("=" * 60)
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"quit", "exit", "q"}:
            break
        if not question:
            continue
        print("Bot:", answer_question(question))
    print("\nBye!")


if __name__ == "__main__":
    main()

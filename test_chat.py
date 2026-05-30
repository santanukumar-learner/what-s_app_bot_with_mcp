from rag import answer_question  


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

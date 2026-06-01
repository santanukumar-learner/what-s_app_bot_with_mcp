from chatbot import answer


def main():
    print("=" * 64)
    print(f" Claude WhatsApp-style chatbot — local test (no WhatsApp)")
    print("=" * 64)
    phone = input("Enter a phone number to chat as (e.g. +919812345678): ").strip()
    if not phone:
        phone = "+910000000000"
    print(f"\nChatting as {phone}. Type 'quit' to exit, 'switch' to change number.\n")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if message.lower() in {"quit", "exit", "q"}:
            break
        if message.lower() == "switch":
            phone = input("New phone number: ").strip() or phone
            print(f"\nNow chatting as {phone}.\n")
            continue
        if not message:
            continue
        print("Bot:", answer(phone, message), "\n")

    print("\nBye!")


if __name__ == "__main__":
    main()

"""
ingest_user.py
==============
MULTI-USER ingestion. Loads a folder of PDFs and stores them in pgvector,
tagged for ONE specific user (identified by WhatsApp number). Creates the
user if they don't exist yet.

Usage:
    python ingest_user.py --number +9170771xxxxx --name "Acme Corp" --docs docs_acme

Then that user (or their customers) can chat the bot and get answers built
only from these documents.
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from db import Document as DocRow
from db import SessionLocal, get_or_create_user, init_db
from rag_multi import add_user_documents

load_dotenv()

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def main():
    parser = argparse.ArgumentParser(description="Ingest PDFs for one user.")
    parser.add_argument("--number", required=True, help="User's WhatsApp number")
    parser.add_argument("--name", default="", help="User's display name")
    parser.add_argument("--docs", required=True, help="Folder containing the PDFs")
    args = parser.parse_args()

    if not os.path.isdir(args.docs):
        sys.exit(f"[ERROR] Folder '{args.docs}' not found.")

    pdfs = [f for f in os.listdir(args.docs) if f.lower().endswith(".pdf")]
    if not pdfs:
        sys.exit(f"[ERROR] No PDFs found in '{args.docs}'.")

    print("Step 1/4: Ensuring database tables exist")
    init_db()

    print(f"Step 2/4: Looking up / creating user {args.number}")
    user = get_or_create_user(args.number, args.name)
    print(f"          user id = {user.id}")

    print("Step 3/4: Loading + splitting PDFs")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    total_chunks = 0
    with SessionLocal() as session:
        for filename in pdfs:
            path = os.path.join(args.docs, filename)
            print(f"  - {filename}")
            pages = PyPDFLoader(path).load()
            chunks = splitter.split_documents(pages)
            add_user_documents(user.id, chunks)
            session.add(
                DocRow(user_id=user.id, filename=filename, num_chunks=len(chunks))
            )
            total_chunks += len(chunks)
        session.commit()

    print(f"Step 4/4: Done — stored {total_chunks} chunks for user {args.number}.")
    print("That user can now chat the bot and get answers from these docs.")


if __name__ == "__main__":
    main()

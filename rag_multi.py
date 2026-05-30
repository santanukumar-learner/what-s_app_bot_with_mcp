"""
rag_multi.py
============
MULTI-USER Retrieval-Augmented-Generation.

Differences from the single-user rag.py:
  * Embeddings are stored in **pgvector** (inside Postgres), not a local FAISS
    file, so all users share one scalable store.
  * Every chunk is tagged with a `user_id` in its metadata.
  * Retrieval is **filtered by user_id**, so each user only ever sees answers
    built from THEIR OWN documents.

Exposes:
  * add_user_documents(user_id, docs)  -> store a user's chunks
  * answer_for_user(user_id, question) -> answer using only that user's data
"""

import os

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import (
    ChatHuggingFace,
    HuggingFaceEmbeddings,
    HuggingFaceEndpoint,
)
from langchain_postgres import PGVector

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/whatsapp_bot",
)
EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
LLM_REPO_ID = os.getenv("HF_LLM_REPO_ID", "Qwen/Qwen2.5-7B-Instruct")
COLLECTION_NAME = "user_docs"  # one shared pgvector collection for all users

PROMPT_TEMPLATE = """You are a helpful assistant. Use ONLY the context below to
answer the question. If the answer is not in the context, say you don't know
based on the available documents. Keep the answer concise and clear.

Context:
{context}

Question: {question}

Answer:"""

# --- Build shared, expensive objects ONCE (module-level singletons) --------
_embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

_vectorstore = PGVector(
    embeddings=_embeddings,
    collection_name=COLLECTION_NAME,
    connection=DATABASE_URL,
    use_jsonb=True,
)

_prompt = PromptTemplate(
    template=PROMPT_TEMPLATE, input_variables=["context", "question"]
)


def _get_llm():
    if os.getenv("HUGGINGFACEHUB_API_TOKEN") is None:
        raise SystemExit("[ERROR] HUGGINGFACEHUB_API_TOKEN missing in .env")
    endpoint = HuggingFaceEndpoint(
        repo_id=LLM_REPO_ID,
        task="conversational",
        temperature=0.3,
        max_new_tokens=512,
    )
    return ChatHuggingFace(llm=endpoint)


_llm = None  # lazily created so ingestion doesn't need the HF token


def llm():
    global _llm
    if _llm is None:
        _llm = _get_llm()
    return _llm


def _format_docs(docs) -> str:
    return "\n\n".join(doc.page_content for doc in docs)


# --- Public API ------------------------------------------------------------

def add_user_documents(user_id: int, docs: list[Document]) -> int:
    """Tag each chunk with the user_id and store it in pgvector. Returns count."""
    for doc in docs:
        doc.metadata = {**(doc.metadata or {}), "user_id": user_id}
    _vectorstore.add_documents(docs)
    return len(docs)


def answer_for_user(user_id: int, question: str) -> str:
    """Answer a question using ONLY the given user's documents."""
    # The filter is what isolates one user's data from everyone else's.
    retriever = _vectorstore.as_retriever(
        search_kwargs={"k": 3, "filter": {"user_id": {"$eq": user_id}}}
    )

    chain = (
        {"context": retriever | _format_docs, "question": lambda x: x}
        | _prompt
        | llm()
        | StrOutputParser()
    )

    try:
        answer = chain.invoke(question).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}")
        return "Sorry, something went wrong while answering. Please try again."

    if not answer:
        return "I couldn't find an answer in your documents."
    return answer[:1500]

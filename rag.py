

import os

from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_community.vectorstores import FAISS
from langchain_huggingface import (
    ChatHuggingFace,
    HuggingFaceEmbeddings,
    HuggingFaceEndpoint,
)

load_dotenv()

# --- Configuration ---------------------------------------------------------
VECTOR_DB_DIR = "vector_db"
EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
LLM_REPO_ID = os.getenv("HF_LLM_REPO_ID", "Qwen/Qwen2.5-7B-Instruct")

PROMPT_TEMPLATE = """You are a helpful assistant. Use ONLY the context below to
answer the question. If the answer is not in the context, say you don't know
based on the available documents. Keep the answer concise and clear.

Context:
{context}

Question: {question}

Answer:"""


def build_qa_chain():
    """Load the vector DB + LLM and wire them into a RAG question-answer chain."""
    if not os.path.isdir(VECTOR_DB_DIR):
        raise SystemExit(
            f"[ERROR] '{VECTOR_DB_DIR}/' not found. Run `python ingest.py` first."
        )

    if not os.getenv("HUGGINGFACEHUB_API_TOKEN"):
        raise SystemExit(
            "[ERROR] HUGGINGFACEHUB_API_TOKEN is missing in your .env file."
        )

    print("Loading embedding model + vector database ...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    vector_db = FAISS.load_local(
        VECTOR_DB_DIR, embeddings, allow_dangerous_deserialization=True
    )
    retriever = vector_db.as_retriever(search_kwargs={"k": 3})

    print(f"Connecting to HuggingFace LLM '{LLM_REPO_ID}' ...")
    endpoint = HuggingFaceEndpoint(
        repo_id=LLM_REPO_ID,
        task="conversational",
        temperature=0.3,
        max_new_tokens=512,
    )
    llm = ChatHuggingFace(llm=endpoint)

    prompt = PromptTemplate(
        template=PROMPT_TEMPLATE, input_variables=["context", "question"]
    )

    def format_docs(docs):
        """Merge retrieved chunks into one context string for the prompt."""
        return "\n\n".join(doc.page_content for doc in docs)

    qa_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    print("[OK] Chatbot is ready.")
    return qa_chain


# Build the chain once at import time (shared across requests).
qa_chain = build_qa_chain()


def answer_question(question: str) -> str:
    """Run the RAG chain and return a WhatsApp-safe answer string."""
    try:
        answer = qa_chain.invoke(question).strip()
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        print(f"[ERROR] {exc}")
        return "Sorry, something went wrong while answering. Please try again."

    if not answer:
        return "I couldn't find an answer in the documents."

    # WhatsApp hard limit is ~4096 chars; keep well under it.
    return answer[:1500]

"""
mcp_server.py
=============
A standalone **MCP server** that exposes the company knowledge base (the local
FAISS vector DB built by `ingest.py`) as a tool any MCP client can call.

It is the company-docs half of the bot: `chatbot.py` spawns this over stdio (via
`mcp_client.py`) and lets Claude call `search_company_docs` inside its tool-use
loop. Because it speaks plain MCP over stdio, you can also point any other MCP
client at it (e.g. Claude Desktop) — see the README.

Embeddings stay local & free (sentence-transformers); this server never calls an
LLM. It only retrieves the most relevant document chunks for a query.

Run directly for a manual check:
    python mcp_server.py        # waits on stdio for an MCP client
"""

import os
import sys

from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from mcp.server.fastmcp import FastMCP

load_dotenv()

# --- Configuration (mirrors chatbot.py / ingest.py) ------------------------
EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
VECTOR_DB_DIR = "vector_db"
DEFAULT_TOP_K = 4  # how many document chunks to retrieve per question


def _load_vector_db() -> FAISS:
    """Load the FAISS index built by ingest.py (downloads the model on 1st run)."""
    if not os.path.isdir(VECTOR_DB_DIR):
        raise SystemExit(
            f"[ERROR] '{VECTOR_DB_DIR}/' not found. Run `python ingest.py` first "
            "to build the company knowledge base from your PDFs."
        )
    # stderr, not stdout: stdout is the MCP stdio transport and must stay clean.
    print("[mcp_server] Loading embedding model + company knowledge base ...",
          file=sys.stderr, flush=True)
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return FAISS.load_local(
        VECTOR_DB_DIR, embeddings, allow_dangerous_deserialization=True
    )


# Load the expensive objects ONCE at startup, before we begin serving.
_vector_db = _load_vector_db()

mcp = FastMCP("company-docs")


@mcp.tool()
def search_company_docs(query: str, top_k: int = DEFAULT_TOP_K) -> str:
    """Search the company's documents for information relevant to a question.

    Use this for any question about the company, its products, services,
    policies, or documentation. Returns the most relevant excerpts from the
    company's documents, separated by '---'. If nothing relevant is found,
    returns a short notice instead.

    Args:
        query: A natural-language question or set of keywords to look up.
        top_k: How many document excerpts to return (default 4).
    """
    top_k = max(1, min(int(top_k or DEFAULT_TOP_K), 10))
    retriever = _vector_db.as_retriever(search_kwargs={"k": top_k})
    docs = retriever.invoke(query)
    if not docs:
        return "(no relevant company documents found)"
    return "\n\n---\n\n".join(doc.page_content for doc in docs)


if __name__ == "__main__":
    # Serve over stdio (the transport chatbot.py / mcp_client.py connects to).
    mcp.run()

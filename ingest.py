import os
import sys

from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

# --- Configuration ---------------------------------------------------------
DOCS_DIR = "docs"               # folder that holds your PDF documents
VECTOR_DB_DIR = "vector_db"     # where the FAISS index is saved
EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
CHUNK_SIZE = 1000               # characters per chunk
CHUNK_OVERLAP = 150             # overlap so context isn't cut mid-sentence


def load_documents(docs_dir: str):
    """Load every PDF in `docs_dir` into a list of LangChain documents."""
    if not os.path.isdir(docs_dir):
        sys.exit(
            f"[ERROR] Folder '{docs_dir}/' not found. "
            f"Create it and put your PDF files inside."
        )

    pdf_files = [f for f in os.listdir(docs_dir) if f.lower().endswith(".pdf")]
    if not pdf_files:
        sys.exit(f"[ERROR] No PDF files found in '{docs_dir}/'.")

    documents = []
    for filename in pdf_files:
        path = os.path.join(docs_dir, filename)
        print(f"  - Loading {filename} ...")
        documents.extend(PyPDFLoader(path).load())

    print(f"[OK] Loaded {len(documents)} pages from {len(pdf_files)} PDF(s).")
    return documents


def main():
    print("Step 1/4: Loading documents")
    documents = load_documents(DOCS_DIR)

    print("Step 2/4: Splitting text into chunks")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    print(f"[OK] Created {len(chunks)} text chunks.")

    print(f"Step 3/4: Loading embedding model '{EMBEDDING_MODEL}'")
    print("        (first run downloads the model — this can take a minute)")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    print("Step 4/4: Building FAISS vector database")
    vector_db = FAISS.from_documents(chunks, embeddings)
    vector_db.save_local(VECTOR_DB_DIR)
    print(f"[DONE] Vector database saved to '{VECTOR_DB_DIR}/'.")
    print("You can now run the chatbot with:  python main.py")


if __name__ == "__main__":
    main()

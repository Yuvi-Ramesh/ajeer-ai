"""
PDF → Qdrant Cloud Pipeline  (Google Gemini embeddings)
========================================================
Reads GEMINI_API_KEY and QDRANT_API_KEY from .env file automatically.

Requirements:
    pip install pdfplumber qdrant-client google-genai tqdm python-dotenv

Usage:
    python pdf_to_qdrant.py --pdf report.pdf
    python pdf_to_qdrant.py --dir ./docs/
    python pdf_to_qdrant.py --pdf report.pdf --query "What is the refund policy?"
"""

import argparse
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
import pdfplumber
from tqdm import tqdm
from google import genai
from google.genai import types as genai_types
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

if not GEMINI_API_KEY:
    raise EnvironmentError("GEMINI_API_KEY not found. Add it to your .env file.")
if not QDRANT_API_KEY:
    raise EnvironmentError("QDRANT_API_KEY not found. Add it to your .env file.")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
QDRANT_HOST = "a20c3626-668a-4022-bd3c-ee44ef2ce718.us-west-1-0.aws.cloud.qdrant.io"
QDRANT_PORT = 6333
COLLECTION_NAME = "ajeer_faq"

EMBED_MODEL = "gemini-embedding-001"  # 3072-dim → matches ajeer_faq
GENERATE_MODEL = "gemini-2.5-flash-lite"  # for RAG answer generation
VECTOR_DIM = 3072

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
EMBED_BATCH = 50
UPSERT_BATCH = 64


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction
# ─────────────────────────────────────────────────────────────────────────────
def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(
                    {
                        "page": i,
                        "text": text,
                        "source": os.path.basename(pdf_path),
                    }
                )
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


def build_chunks(pages: list[dict]) -> list[dict]:
    chunks = []
    for page in pages:
        cleaned = clean_text(page["text"])
        for idx, chunk in enumerate(chunk_text(cleaned, CHUNK_SIZE, CHUNK_OVERLAP)):
            cid = hashlib.md5(
                f"{page['source']}|{page['page']}|{idx}".encode()
            ).hexdigest()
            chunks.append(
                {
                    "chunk_id": cid,
                    "text": chunk,
                    "source": page["source"],
                    "page": page["page"],
                    "chunk_index": idx,
                }
            )
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Gemini embeddings
# ─────────────────────────────────────────────────────────────────────────────
def embed_texts(gemini_client: genai.Client, texts: list[str]) -> list[list[float]]:
    all_vectors = []
    for i in tqdm(range(0, len(texts), EMBED_BATCH), desc="Embedding"):
        batch = texts[i : i + EMBED_BATCH]
        for attempt in range(3):
            try:
                response = gemini_client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                    config=genai_types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=VECTOR_DIM,
                    ),
                )
                all_vectors.extend([e.values for e in response.embeddings])
                break
            except Exception as e:
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    print(f"  [warn] Embed error: {e}. Retrying in {wait}s …")
                    time.sleep(wait)
                else:
                    raise
    return all_vectors


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant helpers
# ─────────────────────────────────────────────────────────────────────────────
def ensure_collection(client: QdrantClient, name: str):
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"[qdrant] ✓ Created collection '{name}' (dim={VECTOR_DIM}, Cosine)")
    else:
        print(f"[qdrant] Using existing collection '{name}'")


def upsert_chunks(qdrant, gemini_client, collection, chunks):
    texts = [c["text"] for c in chunks]
    print(f"[embed] Encoding {len(texts)} chunks with {EMBED_MODEL} …")
    vectors = embed_texts(gemini_client, texts)

    points = [
        PointStruct(
            id=str(uuid.UUID(c["chunk_id"])),
            vector=vec,
            payload={
                "text": c["text"],
                "source": c["source"],
                "page": c["page"],
                "chunk_index": c["chunk_index"],
            },
        )
        for c, vec in zip(chunks, vectors)
    ]

    total = (len(points) + UPSERT_BATCH - 1) // UPSERT_BATCH
    print(f"[qdrant] Upserting {len(points)} points in {total} batch(es) …")
    for i in tqdm(range(0, len(points), UPSERT_BATCH), desc="Uploading"):
        qdrant.upsert(collection_name=collection, points=points[i : i + UPSERT_BATCH])

    print(f"[qdrant] ✓ Stored {len(points)} chunks.")


# ─────────────────────────────────────────────────────────────────────────────
# RAG search + answer with gemini-2.5-flash-lite
# ─────────────────────────────────────────────────────────────────────────────
def rag_search(qdrant, gemini_client, collection, query, top_k=5):
    print(f"\n── RAG query: '{query}' ──")

    # Embed the query
    q_response = gemini_client.models.embed_content(
        model=EMBED_MODEL,
        contents=query,
        config=genai_types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=VECTOR_DIM,
        ),
    )
    q_vector = q_response.embeddings[0].values

    # Retrieve top-k from Qdrant
    results = qdrant.search(
        collection_name=collection,
        query_vector=q_vector,
        limit=top_k,
    )

    if not results:
        print("No results found.")
        return

    # Build context string
    context = "\n\n---\n\n".join(
        f"[Source: {r.payload['source']}, Page {r.payload['page']}]\n{r.payload['text']}"
        for r in results
    )

    # Generate answer
    prompt = (
        "You are a helpful assistant. Answer the question using ONLY the "
        "provided context. If the answer is not in the context, say so.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {query}\n\nAnswer:"
    )
    print(f"[gemini] Generating answer with {GENERATE_MODEL} …\n")
    answer = gemini_client.models.generate_content(
        model=GENERATE_MODEL, contents=prompt
    )
    print("Answer:\n", answer.text)

    print("\nRetrieved chunks:")
    for r in results:
        p = r.payload
        print(f"  [score={r.score:.4f}] {p['source']}  page {p['page']}")
        print(f"  {p['text'][:200]}…\n")


# ─────────────────────────────────────────────────────────────────────────────
# Per-PDF processing
# ─────────────────────────────────────────────────────────────────────────────
def process_pdf(pdf_path, qdrant, gemini_client, collection):
    print(f"\n── Processing: {pdf_path} ──")
    pages = extract_text_from_pdf(str(pdf_path))
    if not pages:
        print("  [warn] No extractable text found — skipping.")
        return
    print(f"  Extracted {len(pages)} page(s).")
    chunks = build_chunks(pages)
    print(f"  Created {len(chunks)} chunk(s).")
    upsert_chunks(qdrant, gemini_client, collection, chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="PDF → Qdrant Cloud  (keys auto-loaded from .env)"
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--pdf", help="Path to a single PDF file")
    group.add_argument(
        "--dir",
        default="reports",
        help="Directory containing PDF files (default: reports/)",
    )

    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Qdrant collection name (default: {COLLECTION_NAME})",
    )
    parser.add_argument(
        "--query", default=None, help="Optional: run a RAG search after ingestion"
    )
    parser.add_argument(
        "--top-k", default=5, type=int, help="Chunks to retrieve for RAG (default: 5)"
    )
    args = parser.parse_args()

    # Clients
    print(f"[gemini] Initialising  embed={EMBED_MODEL}  generate={GENERATE_MODEL}")
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    print(f"[qdrant] Connecting to {QDRANT_HOST} …")
    qdrant = QdrantClient(
        host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY, https=True
    )

    ensure_collection(qdrant, args.collection)

    # Gather PDFs
    if args.pdf:
        pdf_paths = [args.pdf]
    else:
        pdf_paths = sorted(Path(args.dir).rglob("*.pdf"))
        print(f"[scan] Found {len(pdf_paths)} PDF(s) in '{args.dir}'")

    # Ingest
    for p in pdf_paths:
        process_pdf(str(p), qdrant, gemini_client, args.collection)

    print("\n✅ Ingestion complete!")

    if args.query:
        rag_search(qdrant, gemini_client, args.collection, args.query, args.top_k)


if __name__ == "__main__":
    main()

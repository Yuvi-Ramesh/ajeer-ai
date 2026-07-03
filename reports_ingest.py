"""
reports_ingest.py — Ingest Ajeer report files (.xlsx / .csv) into MongoDB + Qdrant
====================================================================================
Reads every sheet of the compliance / customer-activity / transaction-summary
report exports, stores the full structured content in MongoDB, and embeds each
sheet (or each row, for detail sheets) into a Qdrant collection so the AI
chatbot / admin search can semantically query the reports.

Usage:
    python reports_ingest.py /path/to/folder/with/reports
    python reports_ingest.py report1.xlsx report2.xlsx ...

Env vars required (.env):
    MONGO_URI       — mongodb://...
    QDRANT_URL      — e.g. https://xxxx.us-west-1-0.aws.cloud.qdrant.io:6333
    QDRANT_API_KEY  — your Qdrant cloud API key
    GEMINI_API_KEY  — your Google AI / Gemini API key

Can also be called programmatically (e.g. from an admin "Upload Report" route):

    from reports_ingest import ingest_report_file
    ingest_report_file(mongo.db, "/tmp/upload.xlsx", original_filename="compliance-report-....xlsx")
"""

import os
import re
import sys
import glob
import time
import uuid
from datetime import datetime, date

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ajeer_db")
QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

REPORTS_COLLECTION = "reports_raw"
QDRANT_COLLECTION = os.environ.get("QDRANT_REPORTS_COLLECTION", "ajeer_reports")

# MUST match agents.py's embedding config so RAG lookups stay consistent.
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 3072
BATCH_SIZE = 8

# ─────────────────────────────────────────────────────────────────────────────
# Report-type detection
# ─────────────────────────────────────────────────────────────────────────────
REPORT_TYPE_PATTERNS = {
    "compliance": re.compile(r"compliance", re.I),
    "customer_activity": re.compile(r"customer.?activity", re.I),
    "transaction_summary": re.compile(r"transaction.?summary", re.I),
}

# Sheets that are small aggregate tables — embed each sheet as ONE chunk.
# Everything else is treated as a "detail" sheet — embed row by row.
SUMMARY_SHEETS = {
    "Summary",
    "KYC Status Distribution",
    "KYC Status",
    "Alerts by Severity",
    "Alerts by Status",
    "Alerts by Type",
    "By Status",
    "By Corridor",
    "By Payment Method",
}

PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-to-(\d{4}-\d{2}-\d{2})")

# Fallback: infer report_type from the *sheet names* actually present in the
# file, used whenever the filename doesn't contain a recognizable keyword.
# These sets come directly from what reports_analysis.py expects for each
# report type, so they stay in sync with the dashboard queries.
SHEET_SIGNATURES = {
    "compliance": {
        "KYC Status Distribution",
        "Alerts by Severity",
        "Alerts by Status",
        "Alerts by Type",
        "Suspicious Activity",
        "Top Flagged Customers",
    },
    "customer_activity": {
        "Customer Details",
        "KYC Status",
    },
    "transaction_summary": {
        "By Status",
        "By Corridor",
        "By Payment Method",
        "Transaction Details",
    },
}

# Date columns to scan, per report type, when the filename has no period.
DATE_COLUMNS_BY_SHEET = {
    "Transaction Details": "Date",
    "Suspicious Activity": "Date",
    "Customer Details": ["Registration Date", "Last Login"],
}


def detect_report_type(filename: str, sheets: dict = None) -> str:
    for rtype, pattern in REPORT_TYPE_PATTERNS.items():
        if pattern.search(filename):
            return rtype

    # Filename didn't match — fall back to sheet-name signature matching.
    if sheets:
        sheet_names = set(sheets.keys())
        best_type, best_overlap = "unknown", 0
        for rtype, signature in SHEET_SIGNATURES.items():
            overlap = len(sheet_names & signature)
            if overlap > best_overlap:
                best_type, best_overlap = rtype, overlap
        if best_overlap > 0:
            return best_type

    return "unknown"


def detect_period(filename: str, sheets: dict = None):
    m = PERIOD_RE.search(filename)
    if m:
        return m.group(1), m.group(2)

    # Filename had no period — fall back to scanning date columns in the
    # sheets themselves for a min/max range.
    if sheets:
        dates = []
        for sheet_name, cols in DATE_COLUMNS_BY_SHEET.items():
            rows = sheets.get(sheet_name)
            if not rows:
                continue
            col_list = cols if isinstance(cols, list) else [cols]
            for row in rows:
                for col in col_list:
                    val = row.get(col)
                    if not val or val == "Never":
                        continue
                    parsed = pd.to_datetime(val, errors="coerce", dayfirst=False)
                    if pd.notna(parsed):
                        dates.append(parsed)
        if dates:
            return min(dates).date().isoformat(), max(dates).date().isoformat()

    return None, None


def _clean_value(v):
    """Make a cell value JSON/BSON-safe."""
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def read_all_sheets(path: str) -> dict:
    """Return {sheet_name: [row_dict, ...]} for an .xlsx, or {'Sheet1': rows} for .csv."""
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
        rows = [
            {k: _clean_value(v) for k, v in row.items()}
            for row in df.to_dict(orient="records")
        ]
        return {"Sheet1": rows}

    sheets = pd.read_excel(path, sheet_name=None)
    out = {}
    for name, df in sheets.items():
        rows = [
            {k: _clean_value(v) for k, v in row.items()}
            for row in df.to_dict(orient="records")
        ]
        out[name] = rows
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Text-chunk building (for embeddings)
# ─────────────────────────────────────────────────────────────────────────────
def _row_to_text(sheet_name: str, row: dict) -> str:
    parts = [f"{k}: {v}" for k, v in row.items() if v not in (None, "")]
    return f"[{sheet_name}] " + " | ".join(parts)


def build_chunks(report_type: str, period: str, sheets: dict) -> list:
    """
    Returns a list of {text, payload} chunks ready to embed:
      - summary sheets -> 1 chunk per sheet (all rows joined)
      - detail sheets  -> 1 chunk per row
    """
    chunks = []
    for sheet_name, rows in sheets.items():
        if not rows:
            continue
        if sheet_name in SUMMARY_SHEETS:
            lines = [
                ", ".join(f"{k}: {v}" for k, v in r.items() if v not in (None, ""))
                for r in rows
            ]
            text = (
                f"Ajeer {report_type} report — sheet '{sheet_name}' (period {period}):\n"
                + "\n".join(lines)
            )
            chunks.append(
                {
                    "text": text,
                    "payload": {
                        "text": text,
                        "report_type": report_type,
                        "sheet": sheet_name,
                        "period": period,
                        "kind": "summary",
                    },
                }
            )
        else:
            for row in rows:
                text = _row_to_text(sheet_name, row)
                chunks.append(
                    {
                        "text": text,
                        "payload": {
                            "text": text,
                            "report_type": report_type,
                            "sheet": sheet_name,
                            "period": period,
                            "kind": "detail",
                            "row": row,
                        },
                    }
                )
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Embedding + Qdrant upsert
# ─────────────────────────────────────────────────────────────────────────────
def _get_embed_client():
    from google import genai

    return genai.Client(api_key=GEMINI_API_KEY)


def embed_texts(texts: list) -> list:
    from google.genai import types as genai_types

    client = _get_embed_client()
    vectors = []
    for t in texts:
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=t,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        vectors.append(list(result.embeddings[0].values))
    return vectors


def ensure_qdrant_collection(client):
    from qdrant_client.models import VectorParams, Distance

    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"✓ Created Qdrant collection '{QDRANT_COLLECTION}'")


def upsert_chunks_to_qdrant(chunks: list):
    if not QDRANT_URL or not QDRANT_API_KEY:
        print("⚠ QDRANT_URL/QDRANT_API_KEY not set — skipping Qdrant upsert")
        return 0
    if not GEMINI_API_KEY:
        print("⚠ GEMINI_API_KEY not set — skipping Qdrant upsert")
        return 0

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    ensure_qdrant_collection(client)

    points = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        try:
            vectors = embed_texts(texts)
        except Exception as e:
            print(f"  Embedding error, retrying in 5s: {e}")
            time.sleep(5)
            vectors = embed_texts(texts)
        for c, vec in zip(batch, vectors):
            points.append(
                PointStruct(id=str(uuid.uuid4()), vector=vec, payload=c["payload"])
            )
        print(f"  ✓ Embedded {min(i + BATCH_SIZE, len(chunks))}/{len(chunks)} chunks")
        time.sleep(0.3)

    if points:
        client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    return len(points)


# ─────────────────────────────────────────────────────────────────────────────
# Mongo storage
# ─────────────────────────────────────────────────────────────────────────────
def store_raw_in_mongo(
    db, report_type: str, filename: str, period_start, period_end, sheets: dict
):
    doc = {
        "report_type": report_type,
        "filename": filename,
        "period_start": period_start,
        "period_end": period_end,
        "sheets": sheets,
        "ingested_at": datetime.utcnow(),
    }
    # Keep only the latest ingestion per (report_type, period) to avoid duplicates.
    db[REPORTS_COLLECTION].delete_many(
        {
            "report_type": report_type,
            "period_start": period_start,
            "period_end": period_end,
        }
    )
    result = db[REPORTS_COLLECTION].insert_one(doc)
    return result.inserted_id


# ─────────────────────────────────────────────────────────────────────────────
# Main ingest entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def ingest_report_file(db, path: str, original_filename: str = None) -> dict:
    filename = original_filename or os.path.basename(path)
    sheets = read_all_sheets(path)
    print(f"  Sheets found: {list(sheets.keys())}")

    report_type = detect_report_type(filename, sheets)
    period_start, period_end = detect_period(filename, sheets)
    period_label = (
        f"{period_start} to {period_end}" if period_start else "unknown period"
    )

    print(f"\n→ Ingesting {filename}  [type={report_type}, period={period_label}]")

    mongo_id = store_raw_in_mongo(
        db, report_type, filename, period_start, period_end, sheets
    )
    print(f"  ✓ Stored in MongoDB.{REPORTS_COLLECTION} (_id={mongo_id})")

    chunks = build_chunks(report_type, period_label, sheets)
    n_upserted = upsert_chunks_to_qdrant(chunks)
    print(f"  ✓ Upserted {n_upserted} chunks into Qdrant '{QDRANT_COLLECTION}'")

    return {
        "mongo_id": str(mongo_id),
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "sheets": {k: len(v) for k, v in sheets.items()},
        "qdrant_chunks": n_upserted,
    }


def _collect_input_files(args: list) -> list:
    files = []
    for a in args:
        if os.path.isdir(a):
            files += glob.glob(os.path.join(a, "*.xlsx"))
            files += glob.glob(os.path.join(a, "*.csv"))
        else:
            files.append(a)
    return sorted(set(files))


if __name__ == "__main__":
    if not sys.argv[1:]:
        print(
            "Usage: python reports_ingest.py <folder-or-file> [more files...]\n"
            "  e.g. python reports_ingest.py .\\reports\n"
            "  e.g. python reports_ingest.py report1.xlsx report2.xlsx"
        )
        sys.exit(1)

    files = _collect_input_files(sys.argv[1:])
    if not files:
        print("No .xlsx/.csv report files found at the given path(s).")
        sys.exit(1)

    from pymongo import MongoClient

    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    if db is None or db.name in ("admin", "test"):
        db = client["ajeer_db"]

    summary = []
    for f in files:
        try:
            summary.append(ingest_report_file(db, f))
        except Exception as e:
            print(f"  ✗ Failed to ingest {f}: {e}")

    print("\n✅ Ingestion complete.")
    for s in summary:
        print(
            f"   {s['report_type']:<20} {s['period_start']} → {s['period_end']}  "
            f"sheets={s['sheets']}  qdrant_chunks={s['qdrant_chunks']}"
        )

    client.close()

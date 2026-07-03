"""
fix_unknown_report_types.py — One-time migration to repair records that were
ingested with report_type == "unknown" because the source filename didn't
match the compliance/customer_activity/transaction_summary patterns.

This patches BOTH:
  1. MongoDB `reports_raw` documents (used by the dashboard overview/tabs)
  2. Qdrant `ajeer_reports` points (used by the AI chatbot / semantic search)

Run once, after pulling the updated reports_ingest.py:
    python fix_unknown_report_types.py
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient

from reports_ingest import SHEET_SIGNATURES, DATE_COLUMNS_BY_SHEET, QDRANT_COLLECTION
import pandas as pd

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ajeer_db")
QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")


def infer_type_from_sheets(sheet_names: set) -> str:
    best_type, best_overlap = "unknown", 0
    for rtype, signature in SHEET_SIGNATURES.items():
        overlap = len(sheet_names & signature)
        if overlap > best_overlap:
            best_type, best_overlap = rtype, overlap
    return best_type


def infer_period_from_sheets(sheets: dict):
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
                parsed = pd.to_datetime(val, errors="coerce")
                if pd.notna(parsed):
                    dates.append(parsed)
    if dates:
        return min(dates).date().isoformat(), max(dates).date().isoformat()
    return None, None


def fix_mongo(db):
    print("── Fixing MongoDB reports_raw ──")
    fixed = 0
    for doc in db.reports_raw.find({"report_type": "unknown"}):
        sheet_names = set(doc.get("sheets", {}).keys())
        new_type = infer_type_from_sheets(sheet_names)
        if new_type == "unknown":
            print(
                f"  ⚠ Could not infer type for _id={doc['_id']} (sheets={sheet_names})"
            )
            continue

        update = {"report_type": new_type}
        if not doc.get("period_start"):
            p_start, p_end = infer_period_from_sheets(doc.get("sheets", {}))
            if p_start:
                update["period_start"] = p_start
                update["period_end"] = p_end

        db.reports_raw.update_one({"_id": doc["_id"]}, {"$set": update})
        print(
            f"  ✓ _id={doc['_id']} → report_type={new_type}  {update.get('period_start', '')}"
        )
        fixed += 1
    print(f"  Done. Fixed {fixed} Mongo document(s).\n")


def fix_qdrant():
    if not (QDRANT_URL and QDRANT_API_KEY):
        print("── Skipping Qdrant fix: QDRANT_URL/QDRANT_API_KEY not set ──\n")
        return

    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    print("── Fixing Qdrant ajeer_reports ──")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    # Build a sheet -> inferred report_type map by asking: which report_type
    # signature set does this sheet belong to?
    sheet_to_type = {}
    for rtype, sheets in SHEET_SIGNATURES.items():
        for s in sheets:
            sheet_to_type[s] = rtype

    offset = None
    fixed = 0
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="report_type", match=MatchValue(value="unknown"))
                ]
            ),
            limit=200,
            offset=offset,
            with_payload=True,
        )
        if not points:
            break

        for pt in points:
            sheet = pt.payload.get("sheet")
            new_type = sheet_to_type.get(sheet)
            if not new_type:
                continue
            client.set_payload(
                collection_name=QDRANT_COLLECTION,
                payload={"report_type": new_type},
                points=[pt.id],
            )
            fixed += 1

        if offset is None:
            break

    print(f"  Done. Fixed {fixed} Qdrant point(s).\n")


if __name__ == "__main__":
    client = MongoClient(MONGO_URI)
    db = client["ajeer_db"]
    fix_mongo(db)
    client.close()

    fix_qdrant()

    print(
        "✅ Migration complete. Refresh /admin/reports and /behaviour — data should now appear."
    )

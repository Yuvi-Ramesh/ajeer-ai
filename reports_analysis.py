"""
reports_analysis.py — Report Analytics Dashboard for Ajeer
=============================================================
Serves the compliance / customer-activity / transaction-summary report data
that reports_ingest.py stored in MongoDB (`reports_raw`), plus semantic
search over the Qdrant `ajeer_reports` collection.

Register as a Flask Blueprint in app.py:
    from reports_analysis import reports_bp
    app.register_blueprint(reports_bp)

Admin-only UI: GET /admin/reports  (requires session role == "admin")
"""

import os
import tempfile
from functools import wraps

from flask import Blueprint, session, jsonify, render_template, request
from werkzeug.utils import secure_filename

reports_bp = Blueprint("reports", __name__)

RAW_COLLECTION = "reports_raw"
REPORT_TYPES = ["compliance", "customer_activity", "transaction_summary"]
ALLOWED_EXT = {".xlsx", ".csv"}


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"success": False, "message": "Forbidden — admin only"}), 403
        return f(*args, **kwargs)

    return decorated


def _latest(db, report_type: str):
    return db[RAW_COLLECTION].find_one(
        {"report_type": report_type}, sort=[("ingested_at", -1)]
    )


def _sheet(doc, name, default=None):
    if not doc:
        return default if default is not None else []
    return doc.get("sheets", {}).get(name, default if default is not None else [])


def _summary_as_dict(rows: list) -> dict:
    """Turn a 'Summary' sheet (Metric/Value rows) into a flat dict, skipping section headers."""
    out = {}
    for r in rows:
        m, v = r.get("Metric"), r.get("Value")
        if m is None or v is None:
            continue
        out[m] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard page
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/admin/reports")
@admin_required
def reports_dashboard():
    return render_template(
        "reports_dashboard.html", admin_name=session.get("name", "Admin")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Overview — stat cards + latest period label for each report type
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/overview", methods=["GET"])
@admin_required
def overview():
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    db = mongo.db

    compliance = _latest(db, "compliance")
    customers = _latest(db, "customer_activity")
    transactions = _latest(db, "transaction_summary")

    comp_summary = _summary_as_dict(_sheet(compliance, "Summary"))
    cust_summary = _summary_as_dict(_sheet(customers, "Summary"))
    txn_summary = _summary_as_dict(_sheet(transactions, "Summary"))

    def period_of(doc):
        if not doc:
            return None
        return f"{doc.get('period_start')} to {doc.get('period_end')}"

    return jsonify(
        {
            "success": True,
            "compliance": {
                "period": period_of(compliance),
                "summary": comp_summary,
                "ingested_at": (
                    compliance.get("ingested_at").isoformat() if compliance else None
                ),
            },
            "customers": {
                "period": period_of(customers),
                "summary": cust_summary,
                "ingested_at": (
                    customers.get("ingested_at").isoformat() if customers else None
                ),
            },
            "transactions": {
                "period": period_of(transactions),
                "summary": txn_summary,
                "ingested_at": (
                    transactions.get("ingested_at").isoformat()
                    if transactions
                    else None
                ),
            },
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Compliance analytics
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/compliance", methods=["GET"])
@admin_required
def compliance_analytics():
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    doc = _latest(mongo.db, "compliance")
    if not doc:
        return jsonify(
            {"success": False, "message": "No compliance report ingested yet"}
        )

    return jsonify(
        {
            "success": True,
            "period": f"{doc.get('period_start')} to {doc.get('period_end')}",
            "summary": _summary_as_dict(_sheet(doc, "Summary")),
            "kyc_distribution": _sheet(doc, "KYC Status Distribution"),
            "alerts_by_severity": _sheet(doc, "Alerts by Severity"),
            "alerts_by_status": _sheet(doc, "Alerts by Status"),
            "alerts_by_type": _sheet(doc, "Alerts by Type"),
            "suspicious_activity": _sheet(doc, "Suspicious Activity"),
            "top_flagged_customers": _sheet(doc, "Top Flagged Customers"),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Customer activity analytics
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/customers", methods=["GET"])
@admin_required
def customer_analytics():
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    doc = _latest(mongo.db, "customer_activity")
    if not doc:
        return jsonify(
            {"success": False, "message": "No customer activity report ingested yet"}
        )

    details = _sheet(doc, "Customer Details")
    type_counts = {}
    status_counts = {}
    for r in details:
        t = r.get("Type", "Unknown")
        s = r.get("Status", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        status_counts[s] = status_counts.get(s, 0) + 1

    return jsonify(
        {
            "success": True,
            "period": f"{doc.get('period_start')} to {doc.get('period_end')}",
            "summary": _summary_as_dict(_sheet(doc, "Summary")),
            "kyc_status": _sheet(doc, "KYC Status"),
            "customer_type_counts": type_counts,
            "customer_status_counts": status_counts,
            "customers": details,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transaction analytics
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/transactions", methods=["GET"])
@admin_required
def transaction_analytics():
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    doc = _latest(mongo.db, "transaction_summary")
    if not doc:
        return jsonify(
            {"success": False, "message": "No transaction summary report ingested yet"}
        )

    return jsonify(
        {
            "success": True,
            "period": f"{doc.get('period_start')} to {doc.get('period_end')}",
            "summary": _summary_as_dict(_sheet(doc, "Summary")),
            "by_status": _sheet(doc, "By Status"),
            "by_corridor": _sheet(doc, "By Corridor"),
            "by_payment_method": _sheet(doc, "By Payment Method"),
            "transactions": _sheet(doc, "Transaction Details"),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Semantic search over Qdrant `ajeer_reports`
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/search", methods=["POST"])
@admin_required
def search_reports():
    from reports_ingest import QDRANT_COLLECTION, EMBED_DIM
    import os as _os

    body = request.get_json() or {}
    query = (body.get("query") or "").strip()
    report_type = body.get("report_type")
    top_k = int(body.get("top_k", 8))

    if not query:
        return jsonify({"success": False, "message": "query is required"})

    QDRANT_URL = _os.environ.get("QDRANT_URL", "")
    QDRANT_API_KEY = _os.environ.get("QDRANT_API_KEY", "")
    GEMINI_API_KEY = _os.environ.get("GEMINI_API_KEY", "")
    if not (QDRANT_URL and QDRANT_API_KEY and GEMINI_API_KEY):
        return jsonify(
            {"success": False, "message": "Qdrant/Gemini not configured on server"}
        )

    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=GEMINI_API_KEY)
    vec = (
        client.models.embed_content(
            model="gemini-embedding-001",
            contents=query,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        .embeddings[0]
        .values
    )

    qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    query_filter = None
    if report_type:
        query_filter = Filter(
            must=[
                FieldCondition(key="report_type", match=MatchValue(value=report_type))
            ]
        )

    hits = qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=list(vec),
        limit=top_k,
        query_filter=query_filter,
    ).points

    results = [
        {
            "score": h.score,
            "text": h.payload.get("text"),
            "report_type": h.payload.get("report_type"),
            "sheet": h.payload.get("sheet"),
            "period": h.payload.get("period"),
        }
        for h in hits
    ]
    return jsonify({"success": True, "query": query, "results": results})


# ─────────────────────────────────────────────────────────────────────────────
# Upload + ingest a new report file directly from the dashboard
# ─────────────────────────────────────────────────────────────────────────────
@reports_bp.route("/api/admin/reports/upload", methods=["POST"])
@admin_required
def upload_report():
    from flask import current_app
    from flask_pymongo import PyMongo
    from reports_ingest import ingest_report_file

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    f = request.files["file"]
    filename = secure_filename(f.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return (
            jsonify(
                {"success": False, "message": "Only .xlsx and .csv files are supported"}
            ),
            400,
        )

    mongo = PyMongo(current_app)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = ingest_report_file(mongo.db, tmp_path, original_filename=filename)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

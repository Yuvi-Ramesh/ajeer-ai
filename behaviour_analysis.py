"""
behaviour_analysis.py — AI-Powered Platform Behaviour & Risk Intelligence for Ajeer
======================================================================================
This dashboard no longer relies on synthetic per-user "remittance_logs" demo data.
Every figure it shows is read live from the Qdrant `ajeer_reports` collection — the
same vector store that reports_ingest.py populates from the real compliance /
customer-activity / transaction-summary exports, and that powers the admin report
search and the RAG chatbot. That makes this page a genuine, auditable reflection of
the latest ingested platform reports rather than a per-user simulation.

Pipeline:
  1. Scroll every point's payload out of Qdrant collection `ajeer_reports`.
  2. For each report type (transaction_summary / customer_activity / compliance),
     keep only the most recently ingested period.
  3. Reconstruct sheet-level rows:
       - "detail" chunks already carry a structured `row` dict in their payload.
       - "summary" chunks carry one joined text blob per sheet; we parse that back
         into row dicts (same "key: value, key2: value2" shape reports_ingest.py
         wrote it in).
  4. Aggregate into chart-ready {label, value} pairs and headline KPIs.
  5. Hand a compact, numeric snapshot to Gemini and ask for a structured,
     analyst-style narrative (executive summary, patterns, risk signals,
     recommendations) — returned as JSON so the frontend can render a proper
     report layout instead of a single paragraph.

Register as a Flask Blueprint in app.py:
    from behaviour_analysis import behaviour_bp
    app.register_blueprint(behaviour_bp)

`log_remittance` is kept as-is (still used by app.py / aml_analysis.py to record
per-transaction events for AML monitoring) — that is a separate concern from the
Qdrant-backed behaviour dashboard below.
"""

import os
import re
import json
from functools import wraps
from datetime import datetime
from collections import defaultdict

from flask import Blueprint, session, jsonify, render_template, request

behaviour_bp = Blueprint("behaviour", __name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config — mirrors reports_ingest.py so both read/write the same collection
# ─────────────────────────────────────────────────────────────────────────────
QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.environ.get("QDRANT_REPORTS_COLLECTION", "ajeer_reports")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GENERATE_MODEL = "gemini-2.5-flash-lite"

# Must match reports_ingest.py's SUMMARY_SHEETS so parsing stays consistent.
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

REPORT_TYPES = ["transaction_summary", "customer_activity", "compliance"]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant access helpers
# ─────────────────────────────────────────────────────────────────────────────
def _qdrant():
    if not (QDRANT_URL and QDRANT_API_KEY):
        return None
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def _scroll_all(client, collection: str):
    """Pull every point's payload out of a Qdrant collection (payload only, no vectors)."""
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def _latest_period(payloads, report_type: str):
    periods = {
        p.get("period")
        for p in payloads
        if p.get("report_type") == report_type and p.get("period")
    }
    periods.discard("unknown period")
    if not periods:
        return None
    # Periods look like "YYYY-MM-DD to YYYY-MM-DD" — sort by the end date.
    return max(periods, key=lambda p: p.split(" to ")[-1])


# ─────────────────────────────────────────────────────────────────────────────
# Row reconstruction / generic aggregation
# ─────────────────────────────────────────────────────────────────────────────
def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_summary_text(text: str):
    """Reconstruct row dicts from a 'summary sheet' chunk's joined text blob."""
    rows = []
    for line in (text or "").split("\n")[1:]:  # skip the sheet header line
        line = line.strip()
        if not line:
            continue
        row = {}
        for part in line.split(", "):
            if ": " in part:
                k, v = part.split(": ", 1)
                row[k.strip()] = v.strip()
        if row:
            rows.append(row)
    return rows


PREFERRED_VALUE_KEYS = ["volume", "amount", "total", "usd", "value", "sum", "count"]
PREFERRED_LABEL_KEYS = [
    "corridor",
    "country",
    "status",
    "method",
    "type",
    "severity",
    "category",
    "name",
]


def _pick_keys(row: dict):
    keys = list(row.keys())
    value_key = None
    for pref in PREFERRED_VALUE_KEYS:
        for k in keys:
            if pref in k.lower() and _num(row.get(k)) is not None:
                value_key = k
                break
        if value_key:
            break
    if not value_key:
        for k in keys:
            if _num(row.get(k)) is not None:
                value_key = k
                break
    label_key = None
    for pref in PREFERRED_LABEL_KEYS:
        for k in keys:
            if pref in k.lower():
                label_key = k
                break
        if label_key:
            break
    if not label_key:
        for k in keys:
            if k != value_key:
                label_key = k
                break
    return label_key, value_key


def _chart_pairs(rows: list, top_n: int = 10):
    """Turn a list of row dicts into sorted [{label, value}] pairs for charting."""
    if not rows:
        return []
    label_key, value_key = _pick_keys(rows[0])
    if not label_key:
        return []
    out = []
    for r in rows:
        label = r.get(label_key)
        if label in (None, ""):
            continue
        val = _num(r.get(value_key)) if value_key else 1
        if val is None:
            val = 1
        out.append({"label": str(label), "value": val})
    out.sort(key=lambda x: -x["value"])
    return out[:top_n]


def _summary_dict(rows: list):
    """Summary sheets follow a Metric/Value convention — flatten to {metric: value}."""
    out = {}
    for r in rows:
        m = r.get("Metric")
        v = r.get("Value")
        if m is not None and v is not None:
            out[m] = v
    return out


def _find_metric(d: dict, *keywords):
    for k, v in d.items():
        lk = k.lower()
        if all(kw in lk for kw in keywords):
            return v
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Gather + aggregate everything from Qdrant
# ─────────────────────────────────────────────────────────────────────────────
def _gather(client):
    all_points = _scroll_all(client, QDRANT_COLLECTION)
    payloads = [p.payload or {} for p in all_points]

    data = {}
    for rtype in REPORT_TYPES:
        period = _latest_period(payloads, rtype)
        sheets = defaultdict(list)
        for p in payloads:
            if p.get("report_type") != rtype:
                continue
            if period and p.get("period") != period:
                continue
            sheet = p.get("sheet", "Unknown")
            if p.get("kind") == "detail":
                row = p.get("row") or {}
                if row:
                    sheets[sheet].append(row)
            else:  # kind == "summary" -> one chunk holding every row for that sheet
                sheets[sheet].extend(_parse_summary_text(p.get("text", "")))
        data[rtype] = {"period": period, "sheets": dict(sheets)}
    return data, len(payloads)


def _build_stats(data: dict):
    txn = data.get("transaction_summary", {"period": None, "sheets": {}})
    cust = data.get("customer_activity", {"period": None, "sheets": {}})
    comp = data.get("compliance", {"period": None, "sheets": {}})

    txn_kpis = _summary_dict(txn["sheets"].get("Summary", []))
    cust_kpis = _summary_dict(cust["sheets"].get("Summary", []))
    comp_kpis = _summary_dict(comp["sheets"].get("Summary", []))

    by_corridor = _chart_pairs(txn["sheets"].get("By Corridor", []))
    by_status = _chart_pairs(txn["sheets"].get("By Status", []))
    by_method = _chart_pairs(txn["sheets"].get("By Payment Method", []))

    txn_details = txn["sheets"].get("Transaction Details", [])
    cust_details = cust["sheets"].get("Customer Details", [])

    type_counts, status_counts = defaultdict(int), defaultdict(int)
    for r in cust_details:
        t = r.get("Type") or r.get("type") or "Unknown"
        s = r.get("Status") or r.get("status") or "Unknown"
        type_counts[t] += 1
        status_counts[s] += 1

    kyc_dist = _chart_pairs(
        comp["sheets"].get("KYC Status Distribution", [])
        or cust["sheets"].get("KYC Status", [])
    )
    alerts_severity = _chart_pairs(comp["sheets"].get("Alerts by Severity", []))
    alerts_status = _chart_pairs(comp["sheets"].get("Alerts by Status", []))
    alerts_type = _chart_pairs(comp["sheets"].get("Alerts by Type", []))

    suspicious = comp["sheets"].get("Suspicious Activity", [])
    top_flagged = comp["sheets"].get("Top Flagged Customers", [])

    total_volume = _find_metric(txn_kpis, "volume") or _find_metric(
        txn_kpis, "total", "amount"
    )
    total_transactions = _find_metric(txn_kpis, "transaction") or (
        len(txn_details) or None
    )
    total_customers = (
        _find_metric(cust_kpis, "total", "customer")
        or _find_metric(cust_kpis, "customer")
        or (len(cust_details) or None)
    )
    avg_transaction = _find_metric(txn_kpis, "average") or _find_metric(txn_kpis, "avg")
    total_alerts = _find_metric(comp_kpis, "alert", "total") or _find_metric(
        comp_kpis, "total"
    )
    top_corridor = by_corridor[0]["label"] if by_corridor else None

    return {
        "period": {
            "transaction_summary": txn.get("period"),
            "customer_activity": cust.get("period"),
            "compliance": comp.get("period"),
        },
        "kpis": {
            "total_volume": total_volume,
            "total_transactions": total_transactions,
            "total_customers": total_customers,
            "avg_transaction": avg_transaction,
            "total_alerts": total_alerts,
            "top_corridor": top_corridor,
        },
        "charts": {
            "by_corridor": by_corridor,
            "by_status": by_status,
            "by_payment_method": by_method,
            "customer_type": [
                {"label": k, "value": v}
                for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
            ],
            "customer_status": [
                {"label": k, "value": v}
                for k, v in sorted(status_counts.items(), key=lambda x: -x[1])
            ],
            "kyc_distribution": kyc_dist,
            "alerts_severity": alerts_severity,
            "alerts_status": alerts_status,
            "alerts_type": alerts_type,
        },
        "tables": {
            "suspicious_activity": suspicious[:15],
            "top_flagged_customers": top_flagged[:15],
        },
        "counts": {
            "transaction_detail_rows": len(txn_details),
            "customer_detail_rows": len(cust_details),
            "suspicious_activity_rows": len(suspicious),
            "top_flagged_rows": len(top_flagged),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI narrative — structured, analyst-style JSON report from Gemini
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_report(message: str):
    return {
        "executive_summary": message,
        "transaction_patterns": "",
        "customer_insights": "",
        "risk_compliance": "",
        "recommendations": [],
    }


def _llm_json(prompt: str) -> dict:
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(model=GENERATE_MODEL, contents=prompt)
    text = (response.text or "").strip()
    text = re.sub(r"^```json\s*|```\s*$", "", text.strip(), flags=re.MULTILINE).strip(
        "` \n"
    )
    return json.loads(text)


def _generate_ai_report(stats: dict) -> dict:
    if not GEMINI_API_KEY:
        return _fallback_report(
            "AI insights unavailable (no GEMINI_API_KEY configured)."
        )

    kpis = stats["kpis"]
    charts = stats["charts"]

    def top3(pairs):
        if not pairs:
            return "no data available"
        return ", ".join(f"{p['label']} ({p['value']:,.0f})" for p in pairs[:3])

    prompt = f"""You are a senior behaviour-analytics and financial-crime analyst at Ajeer, a global
digital-workforce remittance platform. You have just been handed the latest ingested platform
reports (transaction summary, customer activity, and compliance) pulled from the live reporting
database, and you must brief leadership in a short, sharp analyst note.

DATA SNAPSHOT (most recent ingested period per report)
- Total transaction volume: {kpis.get('total_volume') or 'n/a'}
- Total transactions: {kpis.get('total_transactions') or 'n/a'}
- Average transaction size: {kpis.get('avg_transaction') or 'n/a'}
- Total customers on file: {kpis.get('total_customers') or 'n/a'}
- Total compliance alerts: {kpis.get('total_alerts') or 'n/a'}
- Top corridors by volume: {top3(charts['by_corridor'])}
- Transaction status mix: {top3(charts['by_status'])}
- Payment method mix: {top3(charts['by_payment_method'])}
- Customer type mix: {top3(charts['customer_type'])}
- Customer status mix: {top3(charts['customer_status'])}
- KYC distribution: {top3(charts['kyc_distribution'])}
- Alerts by severity: {top3(charts['alerts_severity'])}
- Alerts by type: {top3(charts['alerts_type'])}
- Suspicious-activity records on file: {stats['counts']['suspicious_activity_rows']}
- Top flagged customers on file: {stats['counts']['top_flagged_rows']}

Return ONLY valid JSON — no markdown fences, no commentary before or after — in exactly this shape:
{{
  "executive_summary": "2-3 sentences on overall platform health this period",
  "transaction_patterns": "2-3 sentences on corridor, status, and payment-method patterns and what they imply",
  "customer_insights": "2-3 sentences on customer mix, growth signals, and KYC posture",
  "risk_compliance": "2-3 sentences on alert volume, severity mix, and anything worth escalating",
  "recommendations": ["short actionable recommendation 1", "recommendation 2", "recommendation 3"]
}}

Be specific to the numbers above, direct, and avoid generic filler. If a figure is 'n/a' or 'no data
available', write around it rather than inventing a number."""

    try:
        report = _llm_json(prompt)
        for key in (
            "executive_summary",
            "transaction_patterns",
            "customer_insights",
            "risk_compliance",
        ):
            report.setdefault(key, "")
        report.setdefault("recommendations", [])
        return report
    except Exception as e:
        return _fallback_report(f"AI narrative generation failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@behaviour_bp.route("/behaviour")
@login_required
def behaviour_dashboard():
    """Render the behaviour & risk intelligence dashboard page."""
    return render_template(
        "behaviour.html",
        user_name=session.get("name", "User"),
        user_country=session.get("country", "Unknown"),
        currency_code=session.get("currency_code", "USD"),
    )


@behaviour_bp.route("/api/behaviour/stats", methods=["GET"])
@login_required
def get_behaviour_stats():
    """
    Aggregate real platform behaviour + risk data straight from the Qdrant
    `ajeer_reports` collection and pair it with an AI-generated analyst report.
    """
    client = _qdrant()
    if client is None:
        return jsonify(
            {
                "success": False,
                "message": "Qdrant not configured on server (QDRANT_URL / QDRANT_API_KEY missing).",
            }
        )

    try:
        data, total_points = _gather(client)
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "message": f"Could not read '{QDRANT_COLLECTION}' from Qdrant: {e}",
            }
        )

    if total_points == 0:
        return jsonify(
            {
                "success": False,
                "message": (
                    f"'{QDRANT_COLLECTION}' is empty. Ingest a report via "
                    "reports_ingest.py or the admin Reports upload first."
                ),
            }
        )

    stats = _build_stats(data)
    ai_report = _generate_ai_report(stats)

    return jsonify(
        {
            "success": True,
            "generated_at": datetime.utcnow().isoformat(),
            "source": {
                "collection": QDRANT_COLLECTION,
                "total_points": total_points,
                "periods": stats["period"],
            },
            "stats": stats,
            "ai_report": ai_report,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Remittance event logging — kept for AML monitoring (separate from the
# Qdrant-backed dashboard above). app.py calls log_remittance() directly on
# every currency conversion; this route allows manual/front-end logging too.
# ─────────────────────────────────────────────────────────────────────────────
def log_remittance(
    db,
    user_id: str,
    from_currency: str,
    to_currency: str,
    to_country: str,
    amount_usd: float,
    rate: float = 1.0,
):
    """Insert one remittance event into the `remittance_logs` collection (used by AML checks)."""
    try:
        now = datetime.utcnow()
        db.remittance_logs.insert_one(
            {
                "user_id": str(user_id),
                "from_currency": from_currency,
                "to_currency": to_currency,
                "to_country": to_country,
                "amount_usd": float(amount_usd),
                "rate": float(rate),
                "timestamp": now,
                "hour": now.hour,
                "weekday": now.strftime("%A"),
                "month": now.strftime("%B"),
            }
        )
    except Exception as e:
        print(f"[behaviour] log_remittance error: {e}")


@behaviour_bp.route("/api/behaviour/log", methods=["POST"])
@login_required
def log_manual():
    """
    Manually log a remittance event (e.g. from the frontend after a conversion).
    POST body: { from_currency, to_currency, to_country, amount_usd, rate }
    """
    from flask import current_app
    from flask_pymongo import PyMongo
    from aml_analysis import run_aml_check

    mongo = PyMongo(current_app)

    body = request.get_json() or {}
    from_currency = body.get("from_currency", "USD")
    to_currency = body.get("to_currency", "USD")
    to_country = body.get("to_country", "Unknown")
    amount_usd = float(body.get("amount_usd", 0))
    rate = float(body.get("rate", 1.0))

    log_remittance(
        db=mongo.db,
        user_id=session["user_id"],
        from_currency=from_currency,
        to_currency=to_currency,
        to_country=to_country,
        amount_usd=amount_usd,
        rate=rate,
    )
    run_aml_check(
        db=mongo.db,
        user_id=session["user_id"],
        from_currency=from_currency,
        to_currency=to_currency,
        to_country=to_country,
        amount_usd=amount_usd,
        rate=rate,
    )
    return jsonify({"success": True, "message": "Remittance event logged."})

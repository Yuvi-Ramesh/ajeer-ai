"""
aml_analysis.py — Real-time AML Risk Detection for Ajeer
==========================================================
Deterministic rule engine that scores every remittance transaction for AML
risk signals in real time, persists flags for admin review, and uses Gemini
ONLY to narrate the rule findings into a readable analyst note.

Design principle: the risk score and reasons are 100% rule-based and
auditable. Gemini never decides severity — it only explains what the rules
already found. This keeps the system defensible to a regulator (the score
for a given transaction is reproducible and doesn't depend on an LLM's mood).

Register as a Flask Blueprint in app.py:
    from aml_analysis import aml_bp, run_aml_check
    app.register_blueprint(aml_bp)

Call this in the SAME request as log_remittance, right after it, e.g. in
/api/currency/convert:
    from aml_analysis import run_aml_check
    run_aml_check(mongo.db, session["user_id"], from_cur, to_cur, to_country, amount, rate)

Admin-only UI: GET /admin/aml  (requires session role == "admin")
"""

from flask import Blueprint, session, jsonify, render_template, request
from functools import wraps
from datetime import datetime, timedelta
from bson import ObjectId
import os
import requests as req
from google import genai

aml_bp = Blueprint("aml", __name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GENERATE_MODEL = "gemini-2.5-flash-lite"
_genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ─────────────────────────────────────────────────────────────────────────────
# OpenSanctions — name/identity screening against global sanctions + PEP lists
# ─────────────────────────────────────────────────────────────────────────────
OPENSANCTIONS_API_KEY = os.environ.get("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_BASE = "https://api.opensanctions.org"
SANCTIONS_SCORE_ALERT = 0.70  # flag for admin review
SANCTIONS_SCORE_BLOCK = 0.90  # treat as a hard compliance block
SANCTIONS_RESCREEN_DAYS = 30  # re-screen a user at most this often (cached on user doc)
if OPENSANCTIONS_API_KEY:
    print("[aml] ✓ OpenSanctions configured")
else:
    print(
        "[aml] ⚠ OPENSANCTIONS_API_KEY not set — sanctions/PEP screening will be skipped"
    )


def _llm(prompt: str) -> str:
    if not _genai_client:
        return "AI narrative unavailable (no GEMINI_API_KEY) — see rule_flags for raw reasons."
    try:
        response = _genai_client.models.generate_content(
            model=GENERATE_MODEL, contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        return f"AI narrative generation failed: {e}"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"success": False, "message": "Forbidden — admin only"}), 403
        return f(*args, **kwargs)

    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Configurable thresholds — tune these per Ajeer's actual risk appetite.
# Kept as plain module constants (not buried in functions) so they're easy
# for a compliance officer to find, justify, and change without reading code.
# ─────────────────────────────────────────────────────────────────────────────

# Structuring: many transfers just under a reporting-style threshold, in a
# short window, is a classic smurfing pattern.
STRUCTURING_AMOUNT_CEILING = 1000.0  # USD — "just under" this amount
STRUCTURING_MIN_COUNT = 3  # this many transfers...
STRUCTURING_WINDOW_HOURS = 24  # ...within this window

# Velocity: raw transaction count/volume spike vs the user's own baseline.
VELOCITY_WINDOW_HOURS = 24
VELOCITY_COUNT_HIGH = 5  # ≥5 transactions in the window
VELOCITY_BASELINE_DAYS = 30  # compare against trailing 30-day avg
VELOCITY_SPIKE_MULTIPLIER = 3.0  # today's volume vs baseline daily avg

# Large single transaction (absolute, regardless of history).
LARGE_TXN_USD = 9000.0  # near common $10k reporting thresholds

# New / unusual destination — never sent there before, but a large amount.
NEW_DESTINATION_MIN_USD = 2000.0

# Rapid multiple recipients — sending to many different countries/currency
# pairs in a short window can indicate layering.
MULTI_DESTINATION_WINDOW_HOURS = 24
MULTI_DESTINATION_MIN_COUNTRIES = 3

# Odd-hour burst — multiple transfers during typical low-activity hours
# (local platform time, server clock — adjust if Ajeer normalizes to a TZ).
ODD_HOURS = set(range(0, 5))  # 00:00–04:59
ODD_HOUR_MIN_COUNT = 2

# Round-amount bias — many transactions at suspiciously "round" amounts can
# indicate manual structuring rather than organic remittance behaviour.
ROUND_AMOUNT_WINDOW_HOURS = 24
ROUND_AMOUNT_MIN_COUNT = 3

# Score → severity bucket (sum of all triggered rule weights below)
SEVERITY_LOW = 1
SEVERITY_MEDIUM = 3
SEVERITY_HIGH = 5

# Per-rule weights — adjust independently of the trigger conditions above.
RULE_WEIGHTS = {
    "structuring": 3,
    "velocity_spike": 2,
    "large_transaction": 2,
    "new_destination_large": 2,
    "multi_destination": 2,
    "odd_hour_burst": 1,
    "round_amount_pattern": 1,
    "sanctions_pep_alert": 5,  # ALERT-level name match — high weight but NOT an auto-block
}

FLAG_REVIEW_THRESHOLD = SEVERITY_LOW  # any score ≥ this creates a flag record


# ─────────────────────────────────────────────────────────────────────────────
# Rule engine — pure functions over remittance_logs, no LLM involved.
# Each rule returns (triggered: bool, detail: str) so the reason is always
# human-readable and tied to the exact numbers that fired it.
# ─────────────────────────────────────────────────────────────────────────────


def _rule_structuring(recent_logs: list, new_amount: float) -> tuple:
    window_logs = [
        l for l in recent_logs if l["amount_usd"] < STRUCTURING_AMOUNT_CEILING
    ]
    count = len(window_logs) + (1 if new_amount < STRUCTURING_AMOUNT_CEILING else 0)
    if count >= STRUCTURING_MIN_COUNT:
        total = sum(l["amount_usd"] for l in window_logs) + (
            new_amount if new_amount < STRUCTURING_AMOUNT_CEILING else 0
        )
        return True, (
            f"{count} transfers under ${STRUCTURING_AMOUNT_CEILING:,.0f} within "
            f"{STRUCTURING_WINDOW_HOURS}h (total ${total:,.2f}) — possible structuring/smurfing."
        )
    return False, ""


def _rule_velocity_spike(
    db, user_id: str, recent_logs: list, new_amount: float
) -> tuple:
    count = len(recent_logs) + 1
    if count < VELOCITY_COUNT_HIGH:
        return False, ""

    since_baseline = datetime.utcnow() - timedelta(days=VELOCITY_BASELINE_DAYS)
    baseline_logs = list(
        db.remittance_logs.find(
            {"user_id": str(user_id), "timestamp": {"$gte": since_baseline}}
        )
    )
    baseline_total = sum(l.get("amount_usd", 0) for l in baseline_logs)
    baseline_daily_avg = baseline_total / max(VELOCITY_BASELINE_DAYS, 1)
    today_total = sum(l["amount_usd"] for l in recent_logs) + new_amount

    if (
        baseline_daily_avg > 0
        and today_total >= baseline_daily_avg * VELOCITY_SPIKE_MULTIPLIER
    ):
        return True, (
            f"{count} transfers in {VELOCITY_WINDOW_HOURS}h totalling ${today_total:,.2f} — "
            f"{today_total / baseline_daily_avg:.1f}x this user's {VELOCITY_BASELINE_DAYS}-day "
            f"daily average (${baseline_daily_avg:,.2f})."
        )
    if count >= VELOCITY_COUNT_HIGH * 2:
        # Even without baseline data, an extreme raw count is worth flagging.
        return (
            True,
            f"{count} transfers within {VELOCITY_WINDOW_HOURS}h — high raw velocity.",
        )
    return False, ""


def _rule_large_transaction(new_amount: float) -> tuple:
    if new_amount >= LARGE_TXN_USD:
        return (
            True,
            f"Single transaction of ${new_amount:,.2f} is at/above the ${LARGE_TXN_USD:,.0f} watch threshold.",
        )
    return False, ""


def _rule_new_destination_large(
    db, user_id: str, to_country: str, new_amount: float
) -> tuple:
    if new_amount < NEW_DESTINATION_MIN_USD:
        return False, ""
    prior = db.remittance_logs.find_one(
        {"user_id": str(user_id), "to_country": to_country}
    )
    if prior is None:
        return True, (
            f"First-ever transfer to {to_country} is ${new_amount:,.2f} "
            f"(above ${NEW_DESTINATION_MIN_USD:,.0f} new-destination watch threshold)."
        )
    return False, ""


def _rule_multi_destination(recent_logs: list, to_country: str) -> tuple:
    countries = {l.get("to_country", "Unknown") for l in recent_logs}
    countries.add(to_country)
    if len(countries) >= MULTI_DESTINATION_MIN_COUNTRIES:
        return True, (
            f"Sent to {len(countries)} different countries within "
            f"{MULTI_DESTINATION_WINDOW_HOURS}h ({', '.join(sorted(countries))}) — possible layering."
        )
    return False, ""


def _rule_odd_hour_burst(recent_logs: list) -> tuple:
    now = datetime.utcnow()
    odd_count = sum(1 for l in recent_logs if l.get("hour") in ODD_HOURS)
    if now.hour in ODD_HOURS:
        odd_count += 1
    if odd_count >= ODD_HOUR_MIN_COUNT:
        return (
            True,
            f"{odd_count} transfers during low-activity hours (00:00–05:00 UTC).",
        )
    return False, ""


def _rule_round_amount_pattern(recent_logs: list, new_amount: float) -> tuple:
    def is_round(amt):
        return amt > 0 and amt % 100 == 0

    round_logs = [l for l in recent_logs if is_round(l["amount_usd"])]
    count = len(round_logs) + (1 if is_round(new_amount) else 0)
    if count >= ROUND_AMOUNT_MIN_COUNT:
        return (
            True,
            f"{count} transfers at suspiciously round amounts (multiples of $100) within {ROUND_AMOUNT_WINDOW_HOURS}h.",
        )
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Sanctions / PEP screening — ported from the KYC onboarding service.
#
# IMPORTANT CAVEAT: Ajeer's signup flow only collects name + country today
# (no date of birth, nationality, or document number — those live in a KYC/
# OCR pipeline Ajeer doesn't have yet). Name-only matching against global
# sanctions lists has a meaningfully higher false-positive rate than the
# KYC app's version, which screens with DOB + document number for precision.
# The fields below are wired to accept dob/document_number as soon as
# Ajeer captures them — until then, treat ALERT-level hits as "needs a
# human to disambiguate," not as confirmed matches.
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_dob(raw: str) -> str:
    """Convert any common date format → YYYY-MM-DD for the OpenSanctions API."""
    if not raw:
        return ""
    for fmt in (
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _sanctions_empty(method: str, reason: str = "") -> dict:
    return {
        "hit": False,
        "block": False,
        "score": 0.0,
        "method": method,
        "matches": [],
        "pep_hit": False,
        "pep_score": 0.0,
        "pep_matches": [],
        "datasets_checked": [],
        "error": reason,
        "screened_at": datetime.utcnow().isoformat(),
        "query": {},
    }


def _os_match(
    name, birth_date, nationality, doc_number, dataset, headers, ref_id
) -> dict:
    """POST /match/<dataset> — fuzzy entity match using the FollowTheMoney Person schema."""
    url = f"{OPENSANCTIONS_BASE}/match/{dataset}"
    properties = {"name": [name]}
    if birth_date:
        properties["birthDate"] = [birth_date]
    if nationality:
        properties["nationality"] = [nationality]
    if doc_number:
        properties["passportNumber"] = [doc_number]
        properties["idNumber"] = [doc_number]

    payload = {"queries": {"q1": {"schema": "Person", "properties": properties}}}
    try:
        resp = req.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("responses", {}).get("q1", {}).get("results", [])
        return {"results": results, "error": None}
    except req.exceptions.HTTPError as e:
        print(f"[aml][sanctions][{ref_id}] /match/{dataset} HTTP error: {e}")
        return {"results": [], "error": str(e)}
    except Exception as e:
        print(f"[aml][sanctions][{ref_id}] /match/{dataset} error: {e}")
        return {"results": [], "error": str(e)}


def _os_search(name, dataset, headers, ref_id) -> dict:
    """GET /search/<dataset> — full-text fallback when /match returns nothing."""
    url = f"{OPENSANCTIONS_BASE}/search/{dataset}"
    try:
        resp = req.get(
            url, params={"q": name, "limit": 10}, headers=headers, timeout=15
        )
        resp.raise_for_status()
        return {"results": resp.json().get("results", []), "error": None}
    except Exception as e:
        print(f"[aml][sanctions][{ref_id}] /search/{dataset} error: {e}")
        return {"results": [], "error": str(e)}


def _parse_os_results(raw_results: list) -> dict:
    """Extract top score and a clean match list from raw OpenSanctions results."""
    top_score = 0.0
    matches = []
    for entity in raw_results:
        score = float(entity.get("score", 0))
        if score < 0.40:
            continue
        top_score = max(top_score, score)
        props = entity.get("properties", {})
        matches.append(
            {
                "id": entity.get("id"),
                "name": (props.get("name") or [""])[0],
                "aliases": props.get("alias", [])[:5],
                "score": round(score, 3),
                "schema": entity.get("schema"),
                "topics": entity.get("topics", []),
                "datasets": entity.get("datasets", []),
                "nationality": props.get("nationality", []),
                "birth_date": props.get("birthDate", []),
                "entity_url": f"https://www.opensanctions.org/entities/{entity.get('id')}/",
            }
        )
    matches.sort(key=lambda x: -x["score"])
    return {"top_score": top_score, "matches": matches}


SANCTIONS_DATASETS_CHECKED = [
    "UN Security Council Consolidated",
    "OFAC SDN + Consolidated",
    "BIS Entity List",
    "EU Financial Sanctions",
    "UK HM Treasury (OFSI)",
    "Australia DFAT Consolidated",
    "Australia Listed Terrorist Orgs",
    "Canada SEMA",
    "SECO Switzerland",
    "Interpol Red Notices",
    "World Bank Debarred",
    "Global PEP Database",
]


def run_sanctions_screening(
    ref_id: str, name: str, country: str = "", dob: str = "", document_number: str = ""
) -> dict:
    """
    Screen one person's name (+ optional country/dob/document_number) against
    global sanctions lists and PEP databases via OpenSanctions.

    `ref_id` is just a label for log lines (e.g. a user_id) — not sent to the API.
    Returns a dict: hit, block, score, matches, pep_hit, pep_score, pep_matches.
    """
    if not OPENSANCTIONS_API_KEY:
        return _sanctions_empty("skipped", "API key not configured")
    name = (name or "").strip()
    if not name:
        return _sanctions_empty("skipped", "No name provided")

    birth_date = _normalise_dob(dob)
    headers = {
        "Authorization": f"ApiKey {OPENSANCTIONS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    s_raw = _os_match(
        name, birth_date, country, document_number, "sanctions", headers, ref_id
    )
    p_raw = _os_match(name, birth_date, country, "", "peps", headers, ref_id)

    method = "match"
    if not s_raw["results"]:
        s_raw = _os_search(name, "sanctions", headers, ref_id)
        method = "search_fallback"

    parsed_s = _parse_os_results(s_raw.get("results", []))
    parsed_p = _parse_os_results(p_raw.get("results", []))
    top_s, top_p = parsed_s["top_score"], parsed_p["top_score"]

    result = {
        "hit": top_s >= SANCTIONS_SCORE_ALERT,
        "block": top_s >= SANCTIONS_SCORE_BLOCK,
        "score": round(top_s, 3),
        "method": method,
        "matches": parsed_s["matches"],
        "pep_hit": top_p >= SANCTIONS_SCORE_ALERT,
        "pep_score": round(top_p, 3),
        "pep_matches": parsed_p["matches"],
        "datasets_checked": SANCTIONS_DATASETS_CHECKED,
        "screened_at": datetime.utcnow().isoformat(),
        "error": s_raw.get("error") or p_raw.get("error"),
        "query": {"name": name, "birth_date": birth_date, "nationality": country},
    }

    flag = "BLOCK" if result["block"] else ("HIT" if result["hit"] else "clear")
    pflag = "PEP" if result["pep_hit"] else "clear"
    print(
        f"[aml][sanctions][{ref_id}] {flag} score={top_s:.3f} | pep={pflag} "
        f"score={top_p:.3f} | matches={len(parsed_s['matches'])} pep_matches={len(parsed_p['matches'])}"
    )
    return result


def _get_or_run_sanctions_check(db, user) -> dict:
    """
    Returns this user's sanctions screening result, cached on the user
    document for SANCTIONS_RESCREEN_DAYS so a free-tier OpenSanctions key
    (or rate limit) isn't burned on every single transaction — sanctions
    lists don't change fast enough to need a fresh check per transfer.
    """
    cached = user.get("sanctions_screening")
    if cached and cached.get("screened_at"):
        try:
            age_days = (
                datetime.utcnow() - datetime.fromisoformat(cached["screened_at"])
            ).days
            if age_days < SANCTIONS_RESCREEN_DAYS:
                return cached
        except Exception:
            pass  # fall through and re-screen on a bad/old cache shape

    result = run_sanctions_screening(
        ref_id=str(user.get("_id", "unknown")),
        name=user.get("name", ""),
        country=user.get("country", ""),
    )
    try:
        db.users.update_one(
            {"_id": user["_id"]}, {"$set": {"sanctions_screening": result}}
        )
    except Exception as e:
        print(f"[aml] failed to cache sanctions result on user doc: {e}")
    return result


def _severity_label(score: int) -> str:
    if score >= SEVERITY_HIGH:
        return "high"
    if score >= SEVERITY_MEDIUM:
        return "medium"
    if score >= SEVERITY_LOW:
        return "low"
    return "none"


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point — call this right after log_remittance() in app.py
# ─────────────────────────────────────────────────────────────────────────────
def run_aml_check(
    db,
    user_id: str,
    from_currency: str,
    to_currency: str,
    to_country: str,
    amount_usd: float,
    rate: float = 1.0,
) -> dict:
    """
    Real-time AML check for a single transaction. Two layers:

      1. Deterministic rule engine over remittance_logs (structuring,
         velocity, large/new-destination, layering, odd-hour, round-amount).
         No LLM, no external API — fast and fully reproducible.

      2. Sanctions/PEP screening of the sender's name via OpenSanctions,
         cached per-user for SANCTIONS_RESCREEN_DAYS (see
         _get_or_run_sanctions_check). A BLOCK-level match is an absolute
         override — it sets status to "blocked" regardless of the
         behavioral score, the same way the KYC app treats a sanctions
         block as final rather than just "more points."

    Returns:
      {"flagged": bool, "score": int, "severity": str, "reasons": [str, ...],
       "sanctions_block": bool}

    If flagged, writes a document to `aml_flags` with status "pending_review"
    (or "blocked" if sanctions_block is True). The AI narrative (Gemini) is
    generated lazily, only when an admin opens the flag.
    """
    try:
        since = datetime.utcnow() - timedelta(
            hours=max(
                STRUCTURING_WINDOW_HOURS,
                VELOCITY_WINDOW_HOURS,
                MULTI_DESTINATION_WINDOW_HOURS,
                ROUND_AMOUNT_WINDOW_HOURS,
            )
        )
        recent_logs = list(
            db.remittance_logs.find(
                {"user_id": str(user_id), "timestamp": {"$gte": since}}
            )
        )

        reasons = []
        score = 0

        checks = [
            ("structuring", _rule_structuring(recent_logs, amount_usd)),
            (
                "velocity_spike",
                _rule_velocity_spike(db, user_id, recent_logs, amount_usd),
            ),
            ("large_transaction", _rule_large_transaction(amount_usd)),
            (
                "new_destination_large",
                _rule_new_destination_large(db, user_id, to_country, amount_usd),
            ),
            ("multi_destination", _rule_multi_destination(recent_logs, to_country)),
            ("odd_hour_burst", _rule_odd_hour_burst(recent_logs)),
            (
                "round_amount_pattern",
                _rule_round_amount_pattern(recent_logs, amount_usd),
            ),
        ]

        triggered_rules = []
        for rule_name, (triggered, detail) in checks:
            if triggered:
                score += RULE_WEIGHTS[rule_name]
                reasons.append(detail)
                triggered_rules.append(rule_name)

        # ── Sanctions / PEP screening (cached per-user) ────────────────────
        sanctions_result = None
        sanctions_block = False
        if OPENSANCTIONS_API_KEY:
            user = (
                db.users.find_one({"_id": ObjectId(user_id)})
                if ObjectId.is_valid(user_id)
                else None
            )
            if user:
                sanctions_result = _get_or_run_sanctions_check(db, user)
                if sanctions_result.get("block"):
                    sanctions_block = True
                    triggered_rules.append("sanctions_block")
                    reasons.append(
                        f"Sender name matches a sanctions list entry with score "
                        f"{sanctions_result['score']:.2f} (≥{SANCTIONS_SCORE_BLOCK} block threshold) — "
                        f"transfer must not proceed without compliance sign-off."
                    )
                elif sanctions_result.get("hit") or sanctions_result.get("pep_hit"):
                    score += RULE_WEIGHTS["sanctions_pep_alert"]
                    triggered_rules.append("sanctions_pep_alert")
                    if sanctions_result.get("hit"):
                        reasons.append(
                            f"Sender name partially matches a sanctions list entry "
                            f"(score {sanctions_result['score']:.2f}) — needs manual disambiguation; "
                            f"Ajeer does not yet collect DOB/document number for precise matching."
                        )
                    if sanctions_result.get("pep_hit"):
                        reasons.append(
                            f"Sender name matches a Politically Exposed Person entry "
                            f"(score {sanctions_result['pep_score']:.2f}) — enhanced due diligence recommended."
                        )

        severity = _severity_label(score)
        flagged = score >= FLAG_REVIEW_THRESHOLD or sanctions_block
        status = "blocked" if sanctions_block else "pending_review"
        if sanctions_block:
            severity = "high"  # a sanctions block is never anything less than high

        if flagged:
            db.aml_flags.insert_one(
                {
                    "user_id": str(user_id),
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "to_country": to_country,
                    "amount_usd": float(amount_usd),
                    "rate": float(rate),
                    "score": score,
                    "severity": severity,
                    "triggered_rules": triggered_rules,
                    "reasons": reasons,
                    "sanctions_result": sanctions_result,
                    "ai_narrative": None,  # generated lazily on admin view
                    "status": status,
                    "timestamp": datetime.utcnow(),
                    "reviewed_by": None,
                    "reviewed_at": None,
                    "reviewer_notes": None,
                }
            )

        return {
            "flagged": flagged,
            "score": score,
            "severity": severity,
            "reasons": reasons,
            "sanctions_block": sanctions_block,
        }

    except Exception as e:
        print(f"[aml] run_aml_check error: {e}")
        return {
            "flagged": False,
            "score": 0,
            "severity": "none",
            "reasons": [],
            "sanctions_block": False,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────────────
# AI narrative generator — explains rule findings, never decides severity
# ─────────────────────────────────────────────────────────────────────────────
def _generate_ai_narrative(flag: dict, user_name: str, user_country: str) -> str:
    reasons_block = "\n".join(f"- {r}" for r in flag.get("reasons", []))
    has_sanctions_signal = any(
        r in flag.get("triggered_rules", [])
        for r in ("sanctions_block", "sanctions_pep_alert")
    )
    sanctions_caveat = (
        "\nIMPORTANT: one of the findings is a name-based sanctions/PEP match. Ajeer currently "
        "screens by name and country only (no date of birth or document number), so a name match "
        "alone is NOT confirmation of identity — common names produce false positives. Your note "
        "must explicitly recommend confirming the match against the user's date of birth and "
        "document number before any account action, not just accept the name match at face value.\n"
        if has_sanctions_signal
        else ""
    )

    prompt = f"""You are an AML compliance analyst assistant for Ajeer, a regulated remittance platform.
A rule engine has already detected the following risk signals on one transaction — your job is
ONLY to write a clear, professional analyst note explaining what was found and what an investigator
should check next. Do NOT invent additional risk factors, do NOT change the severity, and do NOT
make a final determination of guilt — only a licensed compliance officer can do that.
{sanctions_caveat}
User: {user_name} from {user_country}
Transaction: {flag.get('amount_usd', 0):,.2f} USD, {flag.get('from_currency')} → {flag.get('to_currency')}, destination: {flag.get('to_country')}
Rule-computed risk score: {flag.get('score')} ({flag.get('severity').upper()} severity)

Triggered rule findings:
{reasons_block}

Write a short analyst note (3-5 sentences):
1. Plainly restate what the rules found, in compliance language.
2. Note any plausible benign explanation if one fits the pattern (e.g. salary day, family event).
3. Suggest exactly one concrete next step for the reviewer (e.g. request source-of-funds documentation,
   confirm identity against the sanctions match using DOB/document number, contact user for
   purpose-of-transfer confirmation).
Be factual and neutral. Do not use the words "guilty," "criminal," or "money launderer.\""""

    return _llm(prompt)


# ─────────────────────────────────────────────────────────────────────────────
# Admin routes
# ─────────────────────────────────────────────────────────────────────────────
@aml_bp.route("/admin/aml")
@admin_required
def aml_dashboard():
    """Render the admin-only AML review dashboard page."""
    return render_template(
        "aml_dashboard.html", admin_name=session.get("name", "Admin")
    )


@aml_bp.route("/api/admin/aml/flags", methods=["GET"])
@admin_required
def list_flags():
    """List AML flags, most recent first. Optional ?status=pending_review|cleared|escalated"""
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    status = request.args.get("status")
    query = {"status": status} if status else {}

    flags = list(mongo.db.aml_flags.find(query).sort("timestamp", -1).limit(200))

    user_ids = list({f["user_id"] for f in flags})
    users = (
        {
            str(u["_id"]): u
            for u in mongo.db.users.find(
                {"_id": {"$in": [ObjectId(uid) for uid in user_ids]}}
            )
        }
        if user_ids
        else {}
    )

    out = []
    for f in flags:
        u = users.get(f["user_id"], {})
        out.append(
            {
                "id": str(f["_id"]),
                "user_id": f["user_id"],
                "user_name": u.get("name", "Unknown"),
                "user_email": u.get("email", ""),
                "from_currency": f.get("from_currency"),
                "to_currency": f.get("to_currency"),
                "to_country": f.get("to_country"),
                "amount_usd": f.get("amount_usd"),
                "score": f.get("score"),
                "severity": f.get("severity"),
                "triggered_rules": f.get("triggered_rules", []),
                "reasons": f.get("reasons", []),
                "status": f.get("status"),
                "timestamp": (
                    f.get("timestamp").isoformat() if f.get("timestamp") else None
                ),
            }
        )

    counts = {
        "pending_review": mongo.db.aml_flags.count_documents(
            {"status": "pending_review"}
        ),
        "cleared": mongo.db.aml_flags.count_documents({"status": "cleared"}),
        "escalated": mongo.db.aml_flags.count_documents({"status": "escalated"}),
        "high_severity_pending": mongo.db.aml_flags.count_documents(
            {"status": "pending_review", "severity": "high"}
        ),
    }

    return jsonify({"success": True, "flags": out, "counts": counts})


@aml_bp.route("/api/admin/aml/flags/<flag_id>", methods=["GET"])
@admin_required
def get_flag_detail(flag_id):
    """
    Full detail for one flag, including the AI narrative. Generates the
    narrative lazily on first view (and caches it on the document) so
    Gemini is only called for flags an admin actually opens.
    """
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    flag = mongo.db.aml_flags.find_one({"_id": ObjectId(flag_id)})
    if not flag:
        return jsonify({"success": False, "message": "Flag not found"}), 404

    user = mongo.db.users.find_one({"_id": ObjectId(flag["user_id"])}) or {}

    if not flag.get("ai_narrative"):
        narrative = _generate_ai_narrative(
            flag, user.get("name", "Unknown"), user.get("country", "Unknown")
        )
        mongo.db.aml_flags.update_one(
            {"_id": flag["_id"]}, {"$set": {"ai_narrative": narrative}}
        )
        flag["ai_narrative"] = narrative

    # User's recent transaction history for reviewer context
    history = list(
        mongo.db.remittance_logs.find({"user_id": flag["user_id"]})
        .sort("timestamp", -1)
        .limit(20)
    )

    return jsonify(
        {
            "success": True,
            "flag": {
                "id": str(flag["_id"]),
                "user_id": flag["user_id"],
                "user_name": user.get("name", "Unknown"),
                "user_email": user.get("email", ""),
                "user_country": user.get("country", "Unknown"),
                "from_currency": flag.get("from_currency"),
                "to_currency": flag.get("to_currency"),
                "to_country": flag.get("to_country"),
                "amount_usd": flag.get("amount_usd"),
                "rate": flag.get("rate"),
                "score": flag.get("score"),
                "severity": flag.get("severity"),
                "triggered_rules": flag.get("triggered_rules", []),
                "reasons": flag.get("reasons", []),
                "ai_narrative": flag.get("ai_narrative"),
                "status": flag.get("status"),
                "timestamp": (
                    flag.get("timestamp").isoformat() if flag.get("timestamp") else None
                ),
                "reviewed_by": flag.get("reviewed_by"),
                "reviewed_at": (
                    flag.get("reviewed_at").isoformat()
                    if flag.get("reviewed_at")
                    else None
                ),
                "reviewer_notes": flag.get("reviewer_notes"),
            },
            "user_history": [
                {
                    "from_currency": h.get("from_currency"),
                    "to_currency": h.get("to_currency"),
                    "to_country": h.get("to_country"),
                    "amount_usd": h.get("amount_usd"),
                    "timestamp": (
                        h.get("timestamp").isoformat() if h.get("timestamp") else None
                    ),
                }
                for h in history
            ],
        }
    )


@aml_bp.route("/api/admin/aml/flags/<flag_id>/review", methods=["POST"])
@admin_required
def review_flag(flag_id):
    """
    Admin resolves a flag. POST body: { status: "cleared"|"escalated", notes: "..." }
    """
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    body = request.get_json() or {}
    new_status = body.get("status")
    notes = body.get("notes", "")

    if new_status not in ("cleared", "escalated"):
        return (
            jsonify(
                {"success": False, "message": "status must be 'cleared' or 'escalated'"}
            ),
            400,
        )

    result = mongo.db.aml_flags.update_one(
        {"_id": ObjectId(flag_id)},
        {
            "$set": {
                "status": new_status,
                "reviewer_notes": notes,
                "reviewed_by": session.get("name", "Admin"),
                "reviewed_at": datetime.utcnow(),
            }
        },
    )

    if result.matched_count == 0:
        return jsonify({"success": False, "message": "Flag not found"}), 404

    return jsonify({"success": True, "message": f"Flag marked as {new_status}."})


@aml_bp.route("/api/admin/aml/user/<user_id>/summary", methods=["GET"])
@admin_required
def user_aml_summary(user_id):
    """Quick AML history summary for one user — all their flags, regardless of status."""
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    flags = list(mongo.db.aml_flags.find({"user_id": user_id}).sort("timestamp", -1))
    total_flagged_usd = sum(f.get("amount_usd", 0) for f in flags)

    return jsonify(
        {
            "success": True,
            "total_flags": len(flags),
            "total_flagged_usd": round(total_flagged_usd, 2),
            "by_severity": {
                "high": sum(1 for f in flags if f.get("severity") == "high"),
                "medium": sum(1 for f in flags if f.get("severity") == "medium"),
                "low": sum(1 for f in flags if f.get("severity") == "low"),
            },
            "by_status": {
                "pending_review": sum(
                    1 for f in flags if f.get("status") == "pending_review"
                ),
                "cleared": sum(1 for f in flags if f.get("status") == "cleared"),
                "escalated": sum(1 for f in flags if f.get("status") == "escalated"),
            },
        }
    )


@aml_bp.route("/api/admin/aml/sanctions/screen", methods=["POST"])
@admin_required
def sanctions_screen_adhoc():
    """
    Ad-hoc sanctions + PEP screen for any name — doesn't require an existing
    transaction or flag. Useful for a compliance officer doing a quick
    one-off check (e.g. on a name a regulator just asked about).

    Request body (JSON):
        name              str  required
        country           str  optional — nationality/country hint
        date_of_birth     str  optional — improves match precision if known
        document_number   str  optional — improves match precision if known
    """
    if not OPENSANCTIONS_API_KEY:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "OPENSANCTIONS_API_KEY not configured on the server.",
                }
            ),
            503,
        )

    body = request.get_json() or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "name is required"}), 400

    result = run_sanctions_screening(
        ref_id=f"adhoc_{session.get('user_id', 'admin')}",
        name=name,
        country=(body.get("country") or "").strip(),
        dob=(body.get("date_of_birth") or "").strip(),
        document_number=(body.get("document_number") or "").strip(),
    )

    verdict = (
        "BLOCK"
        if result["block"]
        else ("HIT" if result["hit"] else ("PEP_HIT" if result["pep_hit"] else "CLEAR"))
    )
    return jsonify({"success": True, "verdict": verdict, "result": result})


@aml_bp.route("/api/admin/aml/user/<user_id>/sanctions/rescreen", methods=["POST"])
@admin_required
def rescreen_user_sanctions(user_id):
    """
    Force a fresh sanctions/PEP screen on one user, bypassing the
    SANCTIONS_RESCREEN_DAYS cache — e.g. after a sanctions list update,
    or when a reviewer wants current data while working a flag.
    """
    from flask import current_app
    from flask_pymongo import PyMongo

    mongo = PyMongo(current_app)
    user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    if not OPENSANCTIONS_API_KEY:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "OPENSANCTIONS_API_KEY not configured on the server.",
                }
            ),
            503,
        )

    result = run_sanctions_screening(
        ref_id=str(user["_id"]),
        name=user.get("name", ""),
        country=user.get("country", ""),
    )
    mongo.db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"sanctions_screening": result}}
    )

    verdict = (
        "BLOCK"
        if result["block"]
        else ("HIT" if result["hit"] else ("PEP_HIT" if result["pep_hit"] else "CLEAR"))
    )
    return jsonify({"success": True, "verdict": verdict, "result": result})

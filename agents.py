"""
agents.py — Ajeer Multi-Agent RAG System (LangGraph + Qdrant)
=============================================================
Architecture:
  User Query
      │
      ▼
  ① Qdrant RAG  (semantic vector search — primary)
      │  HIGH confidence (≥0.62) → return EXACT answer from DB verbatim
      │  MED  confidence (≥0.48) → pass retrieved context to specialist LLM → short answer
      │
      ▼ miss
  ② MongoDB FAQ  (keyword + text search fallback)
      │  hit → return stored answer directly
      │
      ▼ miss
  ③ LangGraph Multi-Agent  (Supervisor → specialist LLM agents)
      ├── FAQ Agent
      ├── Transfer Agent
      ├── Compliance Agent
      ├── Rewards Agent
      ├── Support Agent
      └── Web Search Agent  (Gemini Google Search grounding)

Ajeer = digital money transfer platform by Monex International Ltd.
Regulated by FCA (FRN: 510848). Based in London, UK.
"""

from __future__ import annotations
import os
import re
import time
from typing import TypedDict, Literal
from difflib import SequenceMatcher
from dotenv import load_dotenv

import google.generativeai as genai
from langgraph.graph import StateGraph, END

# ── New google.genai SDK — used exclusively for embeddings ────────────────────
try:
    from google import genai as genai_new
    from google.genai import types as genai_types

    _GENAI_NEW_AVAILABLE = True
except ImportError:
    _GENAI_NEW_AVAILABLE = False
    print("[Embed] google-genai package not installed — run: pip install google-genai")

# ── Qdrant (optional — graceful fallback if not installed) ────────────────────
try:
    from qdrant_client import QdrantClient

    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False
    print("[Qdrant] qdrant-client not installed — RAG disabled")

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Old SDK still needed by LangGraph internals — suppress the warning
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module="google")
import google.generativeai as _genai_legacy

_genai_legacy.configure(api_key=GEMINI_API_KEY)

# New SDK — used for all direct LLM + embedding calls
if _GENAI_NEW_AVAILABLE:
    _llm_client = genai_new.Client(api_key=GEMINI_API_KEY)
else:
    _llm_client = None

_OUT_OF_SCOPE_MARKER = "<<OUT_OF_SCOPE>>"


# ─────────────────────────────────────────────────────────────────
# Gemini helpers  (new google.genai SDK)
# ─────────────────────────────────────────────────────────────────


def _gemini(system: str, user: str, history: list[dict] | None = None) -> str:
    """Single/multi-turn LLM call via new google.genai SDK."""
    if _llm_client is None:
        return "Service unavailable. Please contact cs@Ajeer.money."
    contents = []
    for h in (history or [])[-6:]:
        role = "user" if h.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]})
    try:
        resp = _llm_client.models.generate_content(
            model=GENERATE_MODEL,
            contents=contents,
        )
        return resp.text.strip()
    except Exception as e:
        print(f"[LLM] Error: {e}")
        return "I'm sorry, I encountered an error. Please contact cs@Ajeer.money."


def _gemini_web_search(query: str) -> str:
    """LLM fallback — no web search tool (deprecated in old SDK). Uses new SDK directly."""
    if _llm_client is None:
        return "Service unavailable. Please contact cs@Ajeer.money."
    try:
        prompt = (
            "You are a helpful assistant for Ajeer users. "
            "Answer the question below clearly and concisely in 1–3 sentences. "
            "If it involves financial, legal, or medical advice, remind the user to consult a professional.\n\n"
            f"Question: {query}"
        )
        resp = _llm_client.models.generate_content(
            model=GENERATE_MODEL,
            contents=prompt,
        )
        return resp.text.strip()
    except Exception as e:
        print(f"[LLM fallback] Error: {e}")
        return "I'm sorry, I wasn't able to find an answer right now. Please contact cs@Ajeer.money for help."


# ─────────────────────────────────────────────────────────────────
# Shared Graph State
# ─────────────────────────────────────────────────────────────────


class AgentState(TypedDict):
    user_message: str
    history: list[dict]
    user_name: str
    user_country: str
    currency_code: str
    currency_symbol: str
    currency_name: str
    route: Literal["faq", "transfer", "compliance", "rewards", "support", "web_search"]
    response: str
    agent_used: str
    agent_emoji: str


# ─────────────────────────────────────────────────────────────────
# Knowledge Bases  (LLM fallback — used only when Qdrant + MongoDB miss)
# ─────────────────────────────────────────────────────────────────

FAQ_KB = """
## What is Ajeer?
Ajeer (trading name of Monex International Limited) is a digital money transfer and
remittance platform. It lets you send money internationally to bank accounts, mobile
wallets, or cash collectors — fast, securely, and with real exchange rates.
Monex International Ltd is authorised by the FCA as a Small Payment Institution (FRN: 510848).
Registered address: 32 Spring Street, Paddington, London, W2 1JA.

## Who can use Ajeer?
Anyone who meets the eligibility criteria — including age (18+), residency, and identity
verification requirements — can use the app to send money.

## What currencies does Ajeer support?
Ajeer lets you hold 6+ currencies and send to 30+ currencies including USD, AED, SAR,
INR, GBP, EUR, PKR, and more. Exchange rates are real market rates with no hidden markups.

## Is Ajeer available on mobile?
Yes. The Ajeer app is available on the App Store and Google Play Store.

## What payout methods are available?
Recipients can receive funds via: bank account transfer, mobile wallet, or cash collector.

## What are the key features?
- Same-day transfers on most major currencies
- Real exchange rates (bank-beating rates)
- Hold 6+ currencies in one account
- eSIM product for travel (instant data in 100+ countries, no physical SIM needed)
- Prepaid Ajeer card (globally accepted, spend anywhere, no minimum balance)
- Freelancer/Non-Resident remittance support
- Repeat transfers with saved beneficiaries

## How do I create an account?
Click Register or Sign up on the Ajeer website or app. You'll need basic
identification documents for electronic identity verification.

## What are the contact details?
Email: cs@Ajeer.money
Post: Compliance Department, Monex International Ltd, 32 Spring Street, Paddington, W2 1JA, UK
"""

TRANSFER_KB = """
## How do I send money?
Step 1: Select the destination country
Step 2: Enter the beneficiary details or create a new beneficiary
Step 3: Choose the payout method (bank account / mobile wallet / cash collector)
Step 4: Review fees and exchange rate (shown upfront before confirming)
Step 5: Confirm the transfer

## How much does it cost?
Fees are shown upfront before you confirm. Exact cost depends on destination, amount,
and payout method. No hidden fees — exchange rates are real market rates.

## How long does a transfer take?
Same-day transfers are available on most major currencies. For EEA bank accounts in
GBP/EUR or another EEA currency, funds arrive by end of next Business Day.
For EEA accounts in non-EEA currencies, within 4 Business Days.

## Can I cancel a transfer?
A transaction can only be cancelled if it has NOT yet been processed. An admin fee of £3
applies. Once processed, transactions cannot be recalled.

## What is the Ajeer Card?
The Ajeer prepaid card is globally accepted (Visa/Mastercard), tracks expenses, has no
minimum balance, and works online and in stores. Spend in your held currencies without conversion fees.

## What is the eSIM product?
Instant data eSIMs for travel in 100+ countries. Activate in seconds, no physical SIM needed.
"""

COMPLIANCE_KB = """
## Why do I need to verify my identity?
Required to comply with AML regulations, CTF regulations, FCA requirements (FRN: 510848),
and UK Money Laundering Regulations 2017.

## What is KYC?
KYC (Know Your Customer) is the process of verifying your identity. Required before
higher transfer limits are available.

## Are my funds safe?
Yes. Customer funds are safeguarded in segregated bank accounts separately from company
funds, per EMR requirements. In insolvency, you are reimbursed in priority to other creditors.

## What is Ajeer's data protection policy?
Ajeer complies with GDPR 2018. DPO: Mr G Kiruba. Personal data processed under
'legitimate interests'. Sensitive personal data is NOT collected. Automated decision-taking
is NOT used. You have rights of access, correction, and objection.

## How is Ajeer regulated?
Trading name of Monex International Limited, FCA-authorised Small Payment Institution
(FRN: 510848). Registered in England and Wales (Co. No. 04974470).
FSCS does NOT cover Ajeer. Unresolved complaints → FOS: www.financialombudsman.org.uk
"""

REWARDS_KB = """
## What is the Ajeer Rewards Program?
Monthly lucky draw for customers who send money internationally via the Ajeer app.
Both senders (Remitters) and receivers (Beneficiaries) enter the draw.

## Who is eligible?
- Individuals aged 18+ and corporates who are UK residents or citizens
- Transactions must be made through the Ajeer app
- Minimum remittance of £100 per transaction to qualify

## How are chances calculated?
£100–£299=1 chance | £300–£499=2 | £500–£999=3 | £1000–£1999=4 | £2000–£3499=5 | £3500+=6 (capped)
Chances do NOT roll over. Multiple transactions in the same month earn more chances.

## What prizes can I win?
Umrah tickets for two, Premium Electronics (smartphones, laptops, tablets),
Lifestyle Rewards (fashion accessories, home appliances, vouchers).
All prizes gifted by Bogo Technologies (Bogo Liv brand).

## Beneficiary bonus
Receive £200+ through Ajeer in a calendar month → 1 month complimentary Bogo Liv Gold membership.
"""

SUPPORT_KB = """
## How do I contact Ajeer support?
Email: cs@Ajeer.money
Post: Compliance Department, Monex International Ltd, 32 Spring Street, Paddington, W2 1JA, UK
Live chat: available 24/7 from the Help Centre icon in the portal.

## How do I make a complaint?
Email cs@Ajeer.money or post to the Compliance Department address.
If unresolved → Financial Ombudsman Service: www.financialombudsman.org.uk
FCA: 0800 111 6768 (freephone).

## How do I close my account?
Give at least 1 month's prior written notice. Remaining funds transferred to your
nominated bank account after deducting amounts owed. All pending trades closed out.

## What if a payment went to the wrong account?
Ajeer is not liable for incorrect details provided but will use reasonable efforts to
recover the payment. Reasonable costs may be charged for recovery attempts.

## What if there was an unauthorised payment?
Notify Ajeer without undue delay (within 13 months) via cs@Ajeer.money.
Ajeer will immediately refund subject to investigation.
"""

_OOS_INSTRUCTION = f"""
IMPORTANT — Out-of-scope detection:
If the user's question is NOT covered by the knowledge base above and is genuinely outside
Ajeer's platform scope (e.g. general world knowledge, news, weather, cooking, health advice,
general finance unrelated to Ajeer), respond with ONLY this exact marker:
{_OUT_OF_SCOPE_MARKER}
Do NOT add any other text if you output this marker. Otherwise answer normally.
"""


# ─────────────────────────────────────────────────────────────────
# ① QDRANT RAG  —  Primary lookup layer
# ─────────────────────────────────────────────────────────────────

QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY_ENV = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "ajeer_faq")
GENERATE_MODEL = "gemini-2.5-flash-lite"

# Embedding — MUST match whatever model was used in seed_qdrant.py
EMBED_MODEL = "gemini-embedding-001"  # 3072-dim, used via google.genai (new SDK)
EMBED_DIM = 3072  # auto-corrected from collection on first connect
RAG_TOP_K = 5

# Cosine similarity thresholds
THRESHOLD_EXACT = 0.75  # FAQ Q&A schema: return stored answer directly
THRESHOLD_SEARCH = 0.50  # minimum score to use any RAG result

_qdrant_client: "QdrantClient | None" = None
_genai_new_client = None  # google.genai client singleton


def _get_genai_client():
    """Lazy singleton for google.genai (new SDK) — used only for embeddings."""
    global _genai_new_client
    if _genai_new_client is not None:
        return _genai_new_client
    if not _GENAI_NEW_AVAILABLE:
        return None
    try:
        _genai_new_client = genai_new.Client(api_key=GEMINI_API_KEY)
        return _genai_new_client
    except Exception as e:
        print(f"[Embed] Failed to create google.genai client: {e}")
        return None


def _embed_query(text: str) -> "list[float] | None":
    """
    Embed using google.genai (new SDK) with gemini-embedding-001.
    This MUST match the model used in seed_qdrant.py.
    """
    client = _get_genai_client()
    if client is None:
        print(
            "[Embed] google.genai client unavailable — install: pip install google-genai"
        )
        return None
    try:
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
            config=genai_types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        vec = result.embeddings[0].values
        if len(vec) != EMBED_DIM:
            print(
                f"[Embed] Dim mismatch: got {len(vec)}, collection expects {EMBED_DIM}"
            )
            return None
        return list(vec)
    except Exception as e:
        print(f"[Embed] Error: {e}")
        return None


def _get_qdrant() -> "QdrantClient | None":
    """Lazy singleton Qdrant client. Auto-reads stored collection dim on first connect."""
    global _qdrant_client, EMBED_DIM
    if not _QDRANT_AVAILABLE:
        return None
    if _qdrant_client is not None:
        return _qdrant_client
    if not QDRANT_URL or not QDRANT_API_KEY_ENV:
        print("[Qdrant] QDRANT_URL / QDRANT_API_KEY not set — RAG disabled")
        return None
    try:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY_ENV)
        client.get_collections()
        _qdrant_client = client

        # Read actual stored vector size from collection
        try:
            info = client.get_collection(QDRANT_COLLECTION)
            # qdrant-client ≥1.7: vectors_config is a dict {name: VectorParams} or VectorParams
            vc = info.config.params.vectors_config
            if isinstance(vc, dict):
                stored_dim = next(iter(vc.values())).size
            else:
                stored_dim = vc.size if hasattr(vc, "size") else EMBED_DIM
            EMBED_DIM = stored_dim
            print(
                f"[Qdrant] ✓ Connected | collection='{QDRANT_COLLECTION}' dim={EMBED_DIM} embed_model='{EMBED_MODEL}'"
            )
        except Exception as e:
            print(
                f"[Qdrant] ✓ Connected (dim detection failed: {e}) — using EMBED_DIM={EMBED_DIM}"
            )

        return client
    except Exception as e:
        print(f"[Qdrant] Connection failed: {e}")
        return None


# ── Chunk-schema context builder ───────────────────────────────────────────────


def _build_context_from_hits(hits: list) -> str:
    """
    Build a context string from Qdrant hits.
    Supports BOTH payload schemas:
      • Document chunks:  {text, source, page, chunk_index}   ← your MT-UAT PDFs
      • FAQ Q&A pairs:    {question, answer, category}        ← ajeer_faq legacy
    """
    chunks = []
    for h in hits:
        p = h.payload or {}
        if p.get("text"):
            # Document chunk schema
            src = p.get("source", "")
            page = p.get("page", "")
            text = p.get("text", "").strip()
            header = f"[Source: {src}, page {page}]" if src else ""
            chunks.append(f"{header}\n{text}".strip())
        elif p.get("answer"):
            # Legacy FAQ Q&A schema
            q = p.get("question", "")
            a = p.get("answer", "")
            chunks.append(f"Q: {q}\nA: {a}")
        else:
            # Unknown schema — dump all non-empty string values
            raw = " | ".join(
                f"{k}: {v}" for k, v in p.items() if isinstance(v, str) and v
            )
            if raw:
                chunks.append(raw)
    return "\n\n---\n\n".join(chunks)


def _infer_category_from_hits(hits: list) -> str:
    """Infer routing category from payload when no explicit 'category' field exists."""
    text_blob = " ".join(
        (h.payload or {}).get("text", "")
        + " "
        + (h.payload or {}).get("question", "")
        + " "
        + (h.payload or {}).get("source", "")
        for h in hits
    ).lower()

    if any(
        k in text_blob
        for k in [
            "transfer",
            "send money",
            "fee",
            "exchange rate",
            "beneficiary",
            "esim",
            "card",
            "payout",
        ]
    ):
        return "transfer"
    if any(
        k in text_blob
        for k in [
            "kyc",
            "aml",
            "identity",
            "gdpr",
            "compliance",
            "fca",
            "verify",
            "fraud",
        ]
    ):
        return "compliance"
    if any(
        k in text_blob for k in ["reward", "prize", "draw", "bogo", "umrah", "lucky"]
    ):
        return "rewards"
    if any(
        k in text_blob
        for k in ["complaint", "support", "contact", "ombudsman", "closure", "wrong"]
    ):
        return "support"
    return "faq"


def lookup_qdrant_rag(message: str) -> "dict | None":
    """
    Semantic RAG lookup. Handles both chunk and FAQ payload schemas.
    Always synthesises — never returns raw chunk text as a 'verbatim answer'.
    """
    client = _get_qdrant()
    if client is None:
        return None

    vector = _embed_query(message)
    if vector is None:
        return None

    try:
        hits = client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=vector,
            limit=RAG_TOP_K,
            score_threshold=THRESHOLD_SEARCH,
            with_payload=True,
        )
        if not hits:
            print(
                f"[Qdrant] No hits above threshold={THRESHOLD_SEARCH} for: '{message[:60]}'"
            )
            return None

        best = hits[0]
        score = round(best.score, 4)
        p = best.payload or {}

        context = _build_context_from_hits(hits)
        category = p.get("category") or _infer_category_from_hits(hits)

        # For FAQ Q&A schema with high confidence, surface the stored answer directly
        if p.get("answer") and score >= THRESHOLD_EXACT:
            return {
                "answer": p["answer"],
                "question": p.get("question", ""),
                "category": category,
                "score": score,
                "mode": "exact",
                "context": context,
            }

        # All other cases (document chunks OR medium-confidence FAQ) → LLM synthesis
        print(
            f"[Qdrant] score={score} hits={len(hits)} category={category} → synthesise"
        )
        return {
            "answer": "",  # will be generated by _rag_synthesise
            "question": p.get("question", p.get("text", "")[:80]),
            "category": category,
            "score": score,
            "mode": "synthesise",
            "context": context,
        }

    except Exception as e:
        print(f"[Qdrant] Search error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# RAG synthesis helper  (used by run_agent for synthesise-mode hits)
# ─────────────────────────────────────────────────────────────────


def _rag_synthesise(user_message: str, context: str, category: str, state: dict) -> str:
    """
    Feed retrieved Qdrant context into Gemini for a short, grounded answer.
    If context is thin, the LLM may use its own Ajeer knowledge to fill gaps.
    """
    persona_map = {
        "transfer": "You are the Ajeer Transfer Agent — expert in money transfers, fees, exchange rates, beneficiaries, Ajeer Card, and eSIM.",
        "compliance": "You are the Ajeer Compliance Agent — expert in KYC/AML, identity verification, GDPR, FCA regulation, and account security.",
        "support": "You are the Ajeer Support Agent — expert in complaints, account issues, wrong transfers, and contacting Ajeer or regulators.",
        "rewards": "You are the Ajeer Rewards Agent — expert in the Rewards Program, monthly draw, prizes, and Bogo Liv Gold membership.",
        "faq": "You are a helpful Ajeer platform assistant with full knowledge of the Ajeer money transfer service.",
    }
    persona = persona_map.get(category, "You are a helpful Ajeer platform assistant.")

    user_ctx = ""
    if state.get("user_name"):
        user_ctx = (
            f"User: {state['user_name']} from {state['user_country']} "
            f"({state['currency_symbol']} {state['currency_code']})."
        )

    has_context = bool(context and context.strip())
    context_block = (
        f"\nRELEVANT CONTEXT FROM DATABASE:\n{context}\n" if has_context else ""
    )

    system = f"""{persona}
{user_ctx}
{context_block}
Answer the user's question directly and concisely in 1–3 sentences.
- Use the database context above as your primary source where available.
- If the context does not fully answer the question, use your knowledge of Ajeer's platform to give an accurate answer.
- Never invent specific numbers, dates, or policies not grounded in fact.
- No greetings, no filler, no preamble."""

    return _gemini(system, user_message, state.get("history"))


# ─────────────────────────────────────────────────────────────────
# ② MONGODB FAQ  —  Secondary fallback
# ─────────────────────────────────────────────────────────────────

FAQ_SIMILARITY_THRESHOLD = 0.45


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z]{3,}", text.lower())
    stopwords = {
        "the",
        "and",
        "for",
        "are",
        "you",
        "your",
        "this",
        "that",
        "with",
        "have",
        "has",
        "been",
        "from",
        "they",
        "will",
        "not",
        "can",
        "may",
        "any",
        "all",
        "also",
        "more",
        "only",
        "when",
        "once",
        "then",
        "than",
        "their",
        "them",
        "into",
        "over",
        "after",
        "before",
        "during",
        "such",
        "each",
        "both",
        "these",
        "those",
        "which",
        "what",
        "how",
        "why",
        "who",
        "where",
        "our",
        "its",
        "via",
        "per",
        "but",
        "out",
        "use",
        "used",
        "make",
        "made",
        "get",
        "one",
        "two",
        "three",
        "new",
    }
    return [w for w in words if w not in stopwords]


def lookup_faq_db(message: str, db) -> "dict | None":
    """
    Search the faq_kb MongoDB collection for a matching Q&A.
    Used as secondary fallback after Qdrant RAG misses.
    """
    if db is None:
        return None
    try:
        query_keywords = _extract_keywords(message)
        best_match = None
        best_score = 0.0

        # MongoDB full-text search
        text_candidates = []
        try:
            cursor = (
                db["faq_kb"]
                .find(
                    {"$text": {"$search": message}},
                    {
                        "score": {"$meta": "textScore"},
                        "question": 1,
                        "answer": 1,
                        "category": 1,
                    },
                )
                .sort([("score", {"$meta": "textScore"})])
                .limit(10)
            )
            text_candidates = list(cursor)
        except Exception:
            pass

        # Keyword overlap search
        keyword_candidates = []
        if query_keywords:
            try:
                keyword_candidates = list(
                    db["faq_kb"]
                    .find(
                        {"keywords": {"$in": query_keywords}},
                        {"question": 1, "answer": 1, "category": 1},
                    )
                    .limit(15)
                )
            except Exception:
                pass

        # Deduplicate and score
        seen_ids = set()
        candidates = []
        for doc in text_candidates + keyword_candidates:
            doc_id = str(doc.get("_id", ""))
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                candidates.append(doc)

        for doc in candidates:
            q = doc.get("question", "")
            sim_score = _similarity(message, q)
            doc_keywords = set(_extract_keywords(q + " " + doc.get("answer", "")))
            query_kw_set = set(query_keywords)
            overlap = (
                len(query_kw_set & doc_keywords) / len(query_kw_set)
                if query_kw_set
                else 0.0
            )
            combined = (sim_score * 0.65) + (overlap * 0.35)
            if combined > best_score:
                best_score = combined
                best_match = doc

        if best_match and best_score >= FAQ_SIMILARITY_THRESHOLD:
            return {
                "answer": best_match["answer"],
                "question": best_match["question"],
                "category": best_match.get("category", "faq"),
                "score": round(best_score, 3),
            }

    except Exception as e:
        print(f"[MongoDB FAQ lookup error] {e}")

    return None


def _category_to_agent_meta(category: str) -> tuple[str, str]:
    return {
        "transfer": ("Transfer Agent", "💸"),
        "compliance": ("Compliance Agent", "🛡️"),
        "support": ("Support Agent", "🎧"),
        "rewards": ("Rewards Agent", "🎁"),
        "faq": ("FAQ Agent", "📋"),
    }.get(category, ("FAQ Agent", "📋"))


# ─────────────────────────────────────────────────────────────────
# ③ LANGGRAPH MULTI-AGENT  —  Final fallback
# ─────────────────────────────────────────────────────────────────


def supervisor_node(state: AgentState) -> AgentState:
    msg = state["user_message"].lower()

    transfer_kw = [
        "send money",
        "transfer",
        "how to send",
        "fee",
        "fees",
        "cost",
        "beneficiary",
        "payout",
        "exchange rate",
        "forward contract",
        "limit order",
        "cancel",
        "wire",
        "esim",
        "card",
        "how long",
        "how much",
        "receive",
        "destination",
        "iban",
        "swift",
        "payment reference",
        "bank statement",
        "reference appear",
        "recipient",
        "blocked",
        "processing",
        "completed",
        "failed",
        "daily limit",
        "on hold",
        "track",
        "history",
        "transaction",
        "amount",
        "refund",
        "cash pickup",
        "schedule",
        "minimum",
    ]
    compliance_kw = [
        "verify",
        "kyc",
        "aml",
        "identity",
        "document",
        "regulated",
        "fca",
        "law",
        "legal",
        "gdpr",
        "data",
        "privacy",
        "safe",
        "suspend",
        "fraud",
        "secure",
        "regulation",
        "compliance",
        "insolvency",
        "safeguard",
        "sanction",
        "phishing",
        "hacked",
        "2fa",
        "two factor",
        "otp",
        "session",
        "cookie",
        "password",
        "login",
        "locked",
    ]
    rewards_kw = [
        "reward",
        "rewards",
        "prize",
        "draw",
        "lucky",
        "chances",
        "win",
        "umrah",
        "bogo",
        "electronics",
        "voucher",
        "points",
        "eligible",
        "beneficiary win",
        "monthly draw",
    ]
    support_kw = [
        "complaint",
        "problem",
        "issue",
        "close account",
        "contact",
        "wrong account",
        "unauthorised",
        "unauthorized",
        "support",
        "help",
        "error",
        "dispute",
        "ombudsman",
        "update details",
        "dpo",
        "compensation",
        "email not",
        "not receiving",
        "maintenance",
    ]

    def match(kw_list):
        return any(kw in msg for kw in kw_list)

    if match(transfer_kw):
        route = "transfer"
    elif match(compliance_kw):
        route = "compliance"
    elif match(rewards_kw):
        route = "rewards"
    elif match(support_kw):
        route = "support"
    else:
        try:
            if _llm_client:
                classify_prompt = f"""Classify this Ajeer platform user message into exactly one category.
Reply with ONLY the single category word.

Categories:
- faq         → general questions about Ajeer, what it is, features, registration
- transfer    → sending money, fees, exchange rates, beneficiaries, card, eSIM, transaction status
- compliance  → KYC, AML, identity, data privacy, GDPR, security, account suspension
- rewards     → rewards program, lucky draw, prizes, Bogo Liv, Umrah
- support     → complaints, account issues, wrong transfers, contact, closure
- web_search  → completely unrelated to Ajeer (news, weather, cooking, general knowledge)

Message: "{state['user_message']}"
Category:"""
                result = _llm_client.models.generate_content(
                    model=GENERATE_MODEL, contents=classify_prompt
                )
                route_raw = result.text.strip().lower().split()[0]
                route = (
                    route_raw
                    if route_raw
                    in [
                        "faq",
                        "transfer",
                        "compliance",
                        "rewards",
                        "support",
                        "web_search",
                    ]
                    else "faq"
                )
            else:
                route = "faq"
        except Exception:
            route = "faq"

    return {
        **state,
        "route": route,
        "response": "",
        "agent_used": "",
        "agent_emoji": "",
    }


def _handle_oos(
    response: str,
    user_message: str,
    state: AgentState,
    agent_used: str,
    agent_emoji: str,
) -> AgentState:
    if _OUT_OF_SCOPE_MARKER in response:
        print("[OOS] KB agent signalled out-of-scope → web search")
        web_response = _gemini_web_search(user_message)
        return {
            **state,
            "response": web_response,
            "agent_used": "Web Search Agent",
            "agent_emoji": "🌐",
            "route": "web_search",
        }
    return {
        **state,
        "response": response,
        "agent_used": agent_used,
        "agent_emoji": agent_emoji,
    }


def faq_agent_node(state: AgentState) -> AgentState:
    system = f"""You are the Ajeer FAQ Agent. Answer the user's question using the knowledge base below.
Give the exact answer in 1–2 sentences only. No greetings or filler. If not covered, say:
"I don't have details on that. Contact cs@Ajeer.money for help."

KNOWLEDGE BASE:
{FAQ_KB}
{_OOS_INSTRUCTION}"""
    return _handle_oos(
        _gemini(system, state["user_message"], state["history"]),
        state["user_message"],
        state,
        "FAQ Agent",
        "📋",
    )


def transfer_agent_node(state: AgentState) -> AgentState:
    system = f"""You are the Ajeer Transfer Agent. Answer questions about sending money, fees, exchange rates,
payout methods, transfer timelines, the Ajeer Card, and eSIM.

User: {state['user_name']} from {state['user_country']} ({state['currency_symbol']} {state['currency_code']})

Give the exact answer in 1–2 sentences only. No filler. For live rates, direct to the app or cs@Ajeer.money.

KNOWLEDGE BASE:
{TRANSFER_KB}
{_OOS_INSTRUCTION}"""
    return _handle_oos(
        _gemini(system, state["user_message"], state["history"]),
        state["user_message"],
        state,
        "Transfer Agent",
        "💸",
    )


def compliance_agent_node(state: AgentState) -> AgentState:
    system = f"""You are the Ajeer Compliance Agent. Answer questions about KYC/AML, identity verification,
data privacy (GDPR), FCA regulation, account security, and fund safeguarding.

Give the exact answer in 1–2 sentences only. No legal advice — explain Ajeer's policies only. No filler.

KNOWLEDGE BASE:
{COMPLIANCE_KB}
{_OOS_INSTRUCTION}"""
    return _handle_oos(
        _gemini(system, state["user_message"], state["history"]),
        state["user_message"],
        state,
        "Compliance Agent",
        "🛡️",
    )


def rewards_agent_node(state: AgentState) -> AgentState:
    system = f"""You are the Ajeer Rewards Agent. Explain the Rewards Program, monthly draw, prizes,
Bogo Liv Gold membership, and eligibility.

Give the exact answer in 1–2 sentences only. No filler.

KNOWLEDGE BASE:
{REWARDS_KB}
{_OOS_INSTRUCTION}"""
    return _handle_oos(
        _gemini(system, state["user_message"], state["history"]),
        state["user_message"],
        state,
        "Rewards Agent",
        "🎁",
    )


def support_agent_node(state: AgentState) -> AgentState:
    system = f"""You are the Ajeer Support Agent. Help with complaints, account issues, wrong transfers,
account closure, and contacting Ajeer or regulators.

Contact info: cs@Ajeer.money | FOS: www.financialombudsman.org.uk | FCA: 0800 111 6768
Give the exact answer in 1–2 sentences only. No filler.

KNOWLEDGE BASE:
{SUPPORT_KB}
{_OOS_INSTRUCTION}"""
    return _handle_oos(
        _gemini(system, state["user_message"], state["history"]),
        state["user_message"],
        state,
        "Support Agent",
        "🎧",
    )


def web_search_agent_node(state: AgentState) -> AgentState:
    print(f"[WEB SEARCH] Handling: '{state['user_message'][:60]}'")
    response = _gemini_web_search(state["user_message"])
    return {
        **state,
        "response": response,
        "agent_used": "Web Search Agent",
        "agent_emoji": "🌐",
    }


def route_decision(state: AgentState) -> str:
    return state.get("route", "faq")


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("faq", faq_agent_node)
    graph.add_node("transfer", transfer_agent_node)
    graph.add_node("compliance", compliance_agent_node)
    graph.add_node("rewards", rewards_agent_node)
    graph.add_node("support", support_agent_node)
    graph.add_node("web_search", web_search_agent_node)
    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_decision,
        {
            "faq": "faq",
            "transfer": "transfer",
            "compliance": "compliance",
            "rewards": "rewards",
            "support": "support",
            "web_search": "web_search",
        },
    )
    for node in ["faq", "transfer", "compliance", "rewards", "support", "web_search"]:
        graph.add_edge(node, END)
    return graph.compile()


# ─────────────────────────────────────────────────────────────────
# Public API — called from Flask app.py
# ─────────────────────────────────────────────────────────────────


def run_agent(
    message: str,
    history: list[dict],
    user_name: str,
    user_country: str,
    currency_code: str,
    currency_symbol: str,
    currency_name: str,
    db=None,
) -> dict:
    """
    Main entry point. Tries each layer in order and returns the first hit.

    Returns dict with keys:
      reply        — the answer string
      agent_used   — display name of the agent/layer that answered
      agent_emoji  — emoji for the UI
      route        — category (faq/transfer/compliance/rewards/support/web_search)
      sources      — list of source labels for the UI
      faq_db_hit   — True if answered from Qdrant or MongoDB (not full LLM)
      rag_score    — cosine similarity score (only when Qdrant answered)
    """

    # ── LAYER 1: Qdrant RAG ───────────────────────────────────────────────────
    try:
        rag_hit = lookup_qdrant_rag(message)
    except Exception as e:
        print(f"[run_agent] Qdrant layer exception: {e}")
        rag_hit = None

    if rag_hit:
        agent_used, agent_emoji = _category_to_agent_meta(rag_hit["category"])
        mode = rag_hit.get("mode", "exact")
        score = rag_hit["score"]
        print(
            f"[QDRANT RAG] score={score} mode={mode} | q='{rag_hit['question'][:60]}'"
        )

        if mode == "exact":
            reply = rag_hit["answer"]
            sources = ["Ajeer FAQ (Qdrant — exact match)"]
        else:
            # Synthesise from retrieved context; fall through to LangGraph if synthesis fails
            try:
                reply = _rag_synthesise(
                    user_message=message,
                    context=rag_hit["context"],
                    category=rag_hit["category"],
                    state={
                        "user_name": user_name,
                        "user_country": user_country,
                        "currency_code": currency_code,
                        "currency_symbol": currency_symbol,
                        "currency_name": currency_name,
                        "history": history,
                    },
                )
            except Exception as e:
                print(
                    f"[run_agent] RAG synthesis exception: {e} — falling to LangGraph"
                )
                reply = ""

            # If synthesis produced a dead-end "no answer" phrase, treat as miss → LangGraph
            _NO_ANSWER_PHRASES = (
                "i don't have a specific answer",
                "i do not have a specific answer",
                "please contact cs@ajeer",
                "i don't have",
            )
            if not reply or any(p in reply.lower() for p in _NO_ANSWER_PHRASES):
                print(
                    f"[QDRANT RAG] Synthesis returned no-answer → falling to LangGraph agents"
                )
                rag_hit = None  # signal to skip return and continue to Layer 3
            else:
                sources = ["Ajeer FAQ (Qdrant RAG)"]

        if rag_hit and reply:
            return {
                "reply": reply,
                "agent_used": agent_used,
                "agent_emoji": agent_emoji,
                "route": rag_hit["category"],
                "sources": sources,
                "faq_db_hit": True,
                "rag_score": score,
            }

    # ── LAYER 2: MongoDB FAQ ──────────────────────────────────────────────────
    try:
        faq_hit = lookup_faq_db(message, db)
    except Exception as e:
        print(f"[run_agent] MongoDB layer exception: {e}")
        faq_hit = None

    if faq_hit:
        agent_used, agent_emoji = _category_to_agent_meta(faq_hit["category"])
        print(f"[MONGO FAQ]  score={faq_hit['score']} | q='{faq_hit['question'][:60]}'")
        return {
            "reply": faq_hit["answer"],
            "agent_used": agent_used,
            "agent_emoji": agent_emoji,
            "route": faq_hit["category"],
            "sources": ["Ajeer FAQ Database (MongoDB)"],
            "faq_db_hit": True,
        }

    # ── LAYER 3: LangGraph multi-agent ───────────────────────────────────────
    print(f"[LLM AGENTS] No RAG/DB hit → LangGraph for: '{message[:60]}'")
    try:
        graph = build_agent_graph()
        result = graph.invoke(
            {
                "user_message": message,
                "history": history,
                "user_name": user_name,
                "user_country": user_country,
                "currency_code": currency_code,
                "currency_symbol": currency_symbol,
                "currency_name": currency_name,
                "route": "faq",
                "response": "",
                "agent_used": "",
                "agent_emoji": "",
            }
        )
        final_route = result.get("route", "faq")
        reply = result.get("response") or ""
        agent_used = result.get("agent_used", "FAQ Agent")
        agent_emoji = result.get("agent_emoji", "📋")
    except Exception as e:
        print(f"[run_agent] LangGraph exception: {e}")
        try:
            reply = _gemini(
                "You are a helpful Ajeer platform assistant. Answer in 1-2 sentences only.",
                message,
            )
        except Exception:
            reply = "Sorry, I'm having trouble right now. Please contact cs@Ajeer.money for help."
        final_route = "faq"
        agent_used = "FAQ Agent"
        agent_emoji = "📋"

    # Blank response guard
    if not reply or not reply.strip():
        try:
            reply = _gemini(
                "You are a helpful Ajeer platform assistant. Answer in 1-2 sentences only.",
                message,
            )
        except Exception:
            reply = "Sorry, I'm having trouble right now. Please contact cs@Ajeer.money for help."

    sources = (
        ["Web Search (Google)"]
        if final_route == "web_search"
        else ["Ajeer Knowledge Base (LLM)"]
    )

    return {
        "reply": reply,
        "agent_used": agent_used,
        "agent_emoji": agent_emoji,
        "route": final_route,
        "sources": sources,
        "faq_db_hit": False,
    }

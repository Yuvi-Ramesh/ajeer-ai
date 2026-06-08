from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
import requests
import os
from datetime import datetime
from functools import wraps
from bson import ObjectId
from dotenv import load_dotenv
from agents import run_agent

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ajeer-secret-key-2024")

app.config["MONGO_URI"] = os.environ.get(
    "MONGO_URI", "mongodb://localhost:27017/ajeer_db"
)
mongo = PyMongo(app)


def db_ok():
    """Returns True if MongoDB is reachable."""
    try:
        mongo.cx.admin.command("ping")
        return True
    except Exception:
        return False


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "your-gemini-api-key")
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY", "your-exchange-api-key")
GENERATE_MODEL = "gemini-2.5-flash-lite"

_genai_client = genai.Client(api_key=GEMINI_API_KEY)


def _llm(prompt: str) -> str:
    """Simple single-turn LLM call via new google.genai SDK."""
    response = _genai_client.models.generate_content(
        model=GENERATE_MODEL,
        contents=prompt,
    )
    return response.text.strip()


COUNTRY_CURRENCY_MAP = {
    "United States": {"code": "USD", "symbol": "$", "name": "US Dollar"},
    "India": {"code": "INR", "symbol": "₹", "name": "Indian Rupee"},
    "United Kingdom": {"code": "GBP", "symbol": "£", "name": "British Pound"},
    "European Union": {"code": "EUR", "symbol": "€", "name": "Euro"},
    "Germany": {"code": "EUR", "symbol": "€", "name": "Euro"},
    "France": {"code": "EUR", "symbol": "€", "name": "Euro"},
    "Japan": {"code": "JPY", "symbol": "¥", "name": "Japanese Yen"},
    "China": {"code": "CNY", "symbol": "¥", "name": "Chinese Yuan"},
    "Canada": {"code": "CAD", "symbol": "CA$", "name": "Canadian Dollar"},
    "Australia": {"code": "AUD", "symbol": "A$", "name": "Australian Dollar"},
    "Brazil": {"code": "BRL", "symbol": "R$", "name": "Brazilian Real"},
    "Mexico": {"code": "MXN", "symbol": "MX$", "name": "Mexican Peso"},
    "South Africa": {"code": "ZAR", "symbol": "R", "name": "South African Rand"},
    "Nigeria": {"code": "NGN", "symbol": "₦", "name": "Nigerian Naira"},
    "Saudi Arabia": {"code": "SAR", "symbol": "﷼", "name": "Saudi Riyal"},
    "UAE": {"code": "AED", "symbol": "د.إ", "name": "UAE Dirham"},
    "Singapore": {"code": "SGD", "symbol": "S$", "name": "Singapore Dollar"},
    "South Korea": {"code": "KRW", "symbol": "₩", "name": "South Korean Won"},
    "Russia": {"code": "RUB", "symbol": "₽", "name": "Russian Ruble"},
    "Switzerland": {"code": "CHF", "symbol": "CHF", "name": "Swiss Franc"},
    "Pakistan": {"code": "PKR", "symbol": "₨", "name": "Pakistani Rupee"},
    "Bangladesh": {"code": "BDT", "symbol": "৳", "name": "Bangladeshi Taka"},
    "Indonesia": {"code": "IDR", "symbol": "Rp", "name": "Indonesian Rupiah"},
    "Malaysia": {"code": "MYR", "symbol": "RM", "name": "Malaysian Ringgit"},
    "Philippines": {"code": "PHP", "symbol": "₱", "name": "Philippine Peso"},
    "Thailand": {"code": "THB", "symbol": "฿", "name": "Thai Baht"},
    "Turkey": {"code": "TRY", "symbol": "₺", "name": "Turkish Lira"},
    "Egypt": {"code": "EGP", "symbol": "E£", "name": "Egyptian Pound"},
    "Argentina": {"code": "ARS", "symbol": "$", "name": "Argentine Peso"},
    "Other": {"code": "USD", "symbol": "$", "name": "US Dollar"},
}

COUNTRIES = sorted(COUNTRY_CURRENCY_MAP.keys())


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def get_exchange_rate(from_currency, to_currency):
    """Get live exchange rate using ExchangeRate-API with API key"""
    try:
        # Using ExchangeRate-API with your API key
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/pair/{from_currency}/{to_currency}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                return data.get("conversion_rate", 1.0)
    except Exception as e:
        print(f"Exchange rate API error: {e}")

    # Fallback to free API if paid one fails
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get("rates", {}).get(to_currency, 1.0)
    except Exception as e:
        print(f"Fallback exchange rate error: {e}")

    return 1.0


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        country = request.form.get("country", "Other")

        if not db_ok():
            error = "Database unavailable. Please check your MongoDB connection."
        elif mongo.db.users.find_one({"email": email}):
            error = "Email already registered."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif not name:
            error = "Name is required."
        else:
            # Get currency info for the selected country
            currency_info = COUNTRY_CURRENCY_MAP.get(
                country, COUNTRY_CURRENCY_MAP["Other"]
            )

            # Clean user data - only store correct fields
            user_data = {
                "name": name,
                "email": email,
                "password": generate_password_hash(password),
                "country": country,
                "currency_code": currency_info["code"],
                "currency_symbol": currency_info["symbol"],
                "currency_name": currency_info["name"],
                "created_at": datetime.utcnow(),
                "role": "user",
            }

            mongo.db.users.insert_one(user_data)
            print(
                f"✓ New user registered: {name} from {country} with currency {currency_info['code']}"
            )
            return redirect(url_for("login"))

    return render_template("register.html", countries=COUNTRIES, error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        country = request.form.get("country", "Other")

        if not db_ok():
            error = "Database unavailable. Please check your MongoDB connection."
        else:
            user = mongo.db.users.find_one({"email": email})
            if user and check_password_hash(user["password"], password):
                # Get currency info based on the country selected at login
                currency_info = COUNTRY_CURRENCY_MAP.get(
                    country, COUNTRY_CURRENCY_MAP["Other"]
                )

                # Update session with correct data
                session["user_id"] = str(user["_id"])
                session["name"] = user.get("name", "User")
                session["email"] = user["email"]
                session["country"] = country
                session["currency_code"] = currency_info["code"]
                session["currency_symbol"] = currency_info["symbol"]
                session["currency_name"] = currency_info["name"]

                # Update user document
                try:
                    db_update = {
                        "country": country,
                        "currency_code": currency_info["code"],
                        "currency_symbol": currency_info["symbol"],
                        "currency_name": currency_info["name"],
                        "last_login": datetime.utcnow(),
                    }

                    mongo.db.users.update_one({"_id": user["_id"]}, {"$set": db_update})

                    mongo.db.login_logs.insert_one(
                        {
                            "user_id": str(user["_id"]),
                            "email": email,
                            "country": country,
                            "timestamp": datetime.utcnow(),
                            "ip": request.remote_addr,
                        }
                    )

                    print(
                        f"✓ User logged in: {user.get('name')} from {country} using {currency_info['code']}"
                    )
                except Exception as e:
                    print(f"DB log error: {e}")

                return redirect(url_for("dashboard"))
            else:
                error = "Invalid email or password."

    return render_template("login.html", countries=COUNTRIES, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    # Always use session data for currency
    currency_info = {
        "code": session.get("currency_code", "USD"),
        "symbol": session.get("currency_symbol", "$"),
        "name": session.get("currency_name", "US Dollar"),
    }
    user = None
    try:
        if mongo.db is not None:
            user = mongo.db.users.find_one({"_id": ObjectId(session["user_id"])})
    except Exception as e:
        print(f"MongoDB user fetch error: {e}")

    if user is None:
        user = {
            "name": session.get("name", "User"),
            "email": session.get("email", ""),
            "country": session.get("country", "Other"),
            "currency_code": session.get("currency_code", "USD"),
            "currency_symbol": session.get("currency_symbol", "$"),
            "currency_name": session.get("currency_name", "US Dollar"),
        }

    # Update session with latest user data
    session["name"] = user.get("name", "User")
    session["country"] = user.get("country", "Other")
    session["currency_code"] = user.get("currency_code", "USD")
    session["currency_symbol"] = user.get("currency_symbol", "$")
    session["currency_name"] = user.get("currency_name", "US Dollar")

    try:
        total_jobs = mongo.db.jobs.count_documents({}) if mongo.db else 0
        active_workers = (
            mongo.db.users.count_documents({"role": "worker"}) if mongo.db else 0
        )
        completed_tasks = (
            mongo.db.tasks.count_documents({"status": "completed"}) if mongo.db else 0
        )
    except Exception:
        total_jobs = active_workers = completed_tasks = 0

    stats = {
        "total_jobs": total_jobs,
        "active_workers": active_workers,
        "completed_tasks": completed_tasks,
        "revenue": 48750,
    }

    currency_info = {
        "code": session.get("currency_code", "USD"),
        "symbol": session.get("currency_symbol", "$"),
        "name": session.get("currency_name", "US Dollar"),
    }

    return render_template(
        "dashboard.html",
        user=user,
        stats=stats,
        currency=currency_info,
        country=session.get("country", "Other"),
        countries=COUNTRIES,
        now_hour=datetime.now().hour,
    )


@app.route("/chatbot")
@login_required
def chatbot():
    currency_info = {
        "code": session.get("currency_code", "USD"),
        "symbol": session.get("currency_symbol", "$"),
        "name": session.get("currency_name", "US Dollar"),
    }
    return render_template(
        "chatbot.html",
        currency=currency_info,
        user_name=session.get("name", "User"),
        user_country=session.get("country", "Other"),
    )


@app.route("/api/user/profile", methods=["GET"])
@login_required
def user_profile():
    """Get user profile"""
    user = mongo.db.users.find_one({"_id": ObjectId(session["user_id"])})
    if user:
        return jsonify(
            {
                "success": True,
                "user": {
                    "name": user.get("name"),
                    "email": user.get("email"),
                    "country": user.get("country"),
                    "currency_code": user.get("currency_code"),
                    "currency_symbol": user.get("currency_symbol"),
                    "currency_name": user.get("currency_name"),
                },
            }
        )
    return jsonify({"success": False, "message": "User not found"})


@app.route("/api/currency/rates", methods=["GET"])
@login_required
def get_rates():
    """Get live exchange rates using ExchangeRate-API with API key"""
    base = request.args.get("base", session.get("currency_code", "USD"))

    try:
        # First try with your API key
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/{base}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                return jsonify(
                    {
                        "success": True,
                        "rates": data.get("conversion_rates", {}),
                        "base": base,
                    }
                )
    except Exception as e:
        print(f"ExchangeRate-API error: {e}")

    # Fallback to free API
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{base}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return jsonify(
                {"success": True, "rates": data.get("rates", {}), "base": base}
            )
    except Exception as e:
        print(f"Fallback API error: {e}")

    return jsonify({"success": False, "message": "Could not fetch rates"})


@app.route("/api/currency/convert", methods=["POST"])
@login_required
def convert_currency():
    """Convert between currencies using live rates"""
    data = request.get_json()
    amount = float(data.get("amount", 0))
    from_cur = data.get("from", "USD")
    to_cur = data.get("to", "USD")

    # Use the ExchangeRate-API for conversion
    try:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/pair/{from_cur}/{to_cur}/{amount}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                return jsonify(
                    {
                        "success": True,
                        "amount": amount,
                        "from": from_cur,
                        "to": to_cur,
                        "rate": data.get("conversion_rate", 1.0),
                        "converted": data.get("conversion_result", amount),
                    }
                )
    except Exception as e:
        print(f"Conversion API error: {e}")

    # Fallback to manual calculation
    rate = get_exchange_rate(from_cur, to_cur)
    return jsonify(
        {
            "success": True,
            "amount": amount,
            "from": from_cur,
            "to": to_cur,
            "rate": rate,
            "converted": round(amount * rate, 2),
        }
    )


@app.route("/api/currency/insight", methods=["POST"])
@login_required
def currency_insight():
    data = request.get_json()
    from_currency = data.get("from_currency", "USD")
    to_currency = data.get("to_currency", "USD")
    rate = data.get("rate", 1.0)
    country = data.get("country", "Unknown")

    prompt = (
        f"You are a concise FX analyst for a workforce platform. "
        f"The current live exchange rate is: 1 {from_currency} = {rate:.4f} {to_currency}. "
        f"The user is based in {country}. "
        f"In exactly 2 sentences: briefly describe the current strength of this rate and give one practical "
        f"tip for a worker or employer sending/receiving money at this rate today. "
        f"Be direct and avoid generic advice."
    )

    try:
        insight = _llm(prompt)

        try:
            if mongo.db is not None:
                mongo.db.currency_insights.insert_one(
                    {
                        "user_id": session["user_id"],
                        "from_currency": from_currency,
                        "to_currency": to_currency,
                        "rate": rate,
                        "insight": insight[:500],
                        "timestamp": datetime.utcnow(),
                    }
                )
        except Exception as db_error:
            print(f"Failed to save insight: {db_error}")

        return jsonify({"success": True, "insight": insight})
    except Exception as e:
        print(f"Gemini insight error: {e}")
        return jsonify(
            {"success": False, "insight": f"Could not generate insight: {str(e)}"}
        )


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json()
    message = data.get("message", "")
    history = data.get("history", [])

    try:
        db = mongo.db if mongo.db is not None else None

        result = run_agent(
            message=message,
            history=history,
            user_name=session.get("name", "User"),
            user_country=session.get("country", "Unknown"),
            currency_code=session.get("currency_code", "USD"),
            currency_symbol=session.get("currency_symbol", "$"),
            currency_name=session.get("currency_name", "US Dollar"),
            db=db,
        )

        reply = result["reply"]

        # Log to MongoDB
        try:
            if mongo.db is not None:
                mongo.db.chat_logs.insert_one(
                    {
                        "user_id": session["user_id"],
                        "message": message,
                        "reply": reply[:500],
                        "agent_used": result.get("agent_used", "Unknown"),
                        "route": result.get("route", "fallback"),
                        "timestamp": datetime.utcnow(),
                    }
                )
        except Exception:
            pass

        return jsonify(
            {
                "success": True,
                "reply": reply,
                "agent_used": result.get("agent_used", ""),
                "sources": result.get("sources", []),
                "route": result.get("route", ""),
                "faq_db_hit": result.get("faq_db_hit", False),
                "rag_score": result.get("rag_score"),
            }
        )

    except Exception as e:
        return jsonify({"success": False, "reply": f"Agent error: {str(e)}"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)

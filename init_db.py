"""
init_db.py — Initialize MongoDB collections and indexes for Ajeer
Run once: python init_db.py
"""

from pymongo import MongoClient, ASCENDING
from werkzeug.security import generate_password_hash
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ajeer_db")

print(f"Connecting to: {MONGO_URI[:50]}...")  # Show first 50 chars (hides password)

client = MongoClient(MONGO_URI)
# Explicitly specify the database name
db = client["ajeer_db"]  # <-- FIXED: Use ajeer_db explicitly


def init():
    print("Initializing Ajeer MongoDB...")

    # Test connection
    try:
        client.admin.command("ping")
        print("✓ Connected to MongoDB successfully")
        print(f"✓ Using database: {db.name}")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        return

    # Indexes
    db.users.create_index([("email", ASCENDING)], unique=True)
    db.jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    db.tasks.create_index([("status", ASCENDING)])
    db.chat_logs.create_index([("user_id", ASCENDING), ("timestamp", ASCENDING)])
    db.login_logs.create_index([("user_id", ASCENDING)])
    db.currency_insights.create_index(
        [("user_id", ASCENDING), ("timestamp", ASCENDING)]
    )
    db.remittance_logs.create_index([("user_id", ASCENDING), ("timestamp", ASCENDING)])
    db.remittance_logs.create_index([("to_country", ASCENDING)])
    print("✓ remittance_logs indexes created")
    db.aml_flags.create_index([("status", ASCENDING), ("timestamp", ASCENDING)])
    db.aml_flags.create_index([("user_id", ASCENDING)])
    db.aml_flags.create_index([("severity", ASCENDING)])
    print("✓ aml_flags indexes created")
    print("✓ Indexes created")

    # Seed admin user
    if not db.users.find_one({"email": "admin@ajeer.com"}):
        db.users.insert_one(
            {
                "name": "Ajeer Admin",
                "email": "admin@ajeer.com",
                "password": generate_password_hash("admin123"),
                "country": "UAE",
                "currency_code": "AED",
                "currency_symbol": "د.إ",
                "currency_name": "UAE Dirham",
                "role": "admin",
                "created_at": datetime.utcnow(),
            }
        )
        print("✓ Admin user seeded: admin@ajeer.com / admin123")

    # Seed sample jobs
    if db.jobs.count_documents({}) == 0:
        jobs = [
            {
                "title": "Construction Foreman",
                "category": "Construction",
                "location": "Dubai",
                "salary_usd": 1800,
                "status": "active",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Nurse (ICU)",
                "category": "Healthcare",
                "location": "Riyadh",
                "salary_usd": 2200,
                "status": "active",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Hotel Receptionist",
                "category": "Hospitality",
                "location": "Abu Dhabi",
                "salary_usd": 900,
                "status": "active",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Electrician",
                "category": "Trades",
                "location": "Doha",
                "salary_usd": 1500,
                "status": "active",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Driver (Heavy Vehicle)",
                "category": "Transportation",
                "location": "Kuwait City",
                "salary_usd": 1100,
                "status": "active",
                "created_at": datetime.utcnow(),
            },
        ]
        db.jobs.insert_many(jobs)
        print(f"✓ Seeded {len(jobs)} sample jobs")

    # Seed sample tasks
    if db.tasks.count_documents({}) == 0:
        tasks = [
            {
                "title": "Install scaffolding",
                "status": "completed",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Patient ward rounds",
                "status": "completed",
                "created_at": datetime.utcnow(),
            },
            {
                "title": "Hotel check-in management",
                "status": "active",
                "created_at": datetime.utcnow(),
            },
        ]
        db.tasks.insert_many(tasks)
        print(f"✓ Seeded {len(tasks)} sample tasks")

    # Create currency_insights collection (ensures it exists)
    if "currency_insights" not in db.list_collection_names():
        db.create_collection("currency_insights")
        print("✓ Created currency_insights collection")

    print("\n✅ Database initialization complete!")
    print(f"   DB: {db.name}")
    print(f"   Collections: {db.list_collection_names()}")

    client.close()


if __name__ == "__main__":
    init()

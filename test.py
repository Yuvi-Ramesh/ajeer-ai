"""
test_mongo.py — Quick MongoDB Atlas connection test
Run: python test_mongo.py
"""

from pymongo import MongoClient
from dotenv import load_dotenv
import os
import sys

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")

if not MONGO_URI:
    print("❌ MONGO_URI not found in .env file!")
    print("   Make sure your .env has: MONGO_URI=mongodb+srv://...")
    sys.exit(1)


# Mask password in printed URI for safety
def mask_uri(uri):
    if "@" in uri:
        parts = uri.split("@")
        creds = parts[0].split("://")[1]
        if ":" in creds:
            user = creds.split(":")[0]
            return uri.replace(creds, f"{user}:****")
    return uri


print(f"🔗 Connecting to: {mask_uri(MONGO_URI)}\n")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

    # Force connection
    client.admin.command("ping")
    print("✅ MongoDB connection successful!\n")

    # List databases
    db_names = client.list_database_names()
    print(f"📦 Databases found: {db_names}\n")

    # Check ajeer_db specifically
    if "ajeer_db" in db_names:
        db = client["ajeer_db"]
        collections = db.list_collection_names()
        print(f"✅ ajeer_db found! Collections: {collections}\n")

        # Count documents in each collection
        print("📊 Document counts:")
        for col in collections:
            count = db[col].count_documents({})
            print(f"   {col}: {count} documents")
    else:
        print("⚠️  ajeer_db not found. Available DBs:", db_names)

    client.close()
    print("\n🎉 All good! Your .env MONGO_URI is working correctly.")

except Exception as e:
    print(f"❌ Connection failed!\n")
    print(f"   Error: {e}\n")
    print("🔧 Troubleshooting tips:")
    print("   1. Check your password in MONGO_URI (no angle brackets like <password>)")
    print("   2. Make sure 0.0.0.0/0 is in Atlas Network Access (IP whitelist)")
    print("   3. Verify your database user exists in Atlas → Database Access")
    print("   4. Ensure the URI includes /ajeer_db before the ?")
    print("      Example: ...mongodb.net/ajeer_db?retryWrites=true&w=majority")

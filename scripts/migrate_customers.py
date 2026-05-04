"""
Export customers + authorized_vehicles from local SQLite to JSON.
Run once from the project root: python scripts/migrate_customers.py
Output: scripts/customer_export.json
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "instance" / "pumpvision.db"
OUT_PATH = Path(__file__).parent / "customer_export.json"

if not DB_PATH.exists():
    raise FileNotFoundError(f"SQLite database not found at {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

customers = [dict(r) for r in conn.execute("SELECT * FROM customers").fetchall()]
vehicles  = [dict(r) for r in conn.execute("SELECT * FROM authorized_vehicles").fetchall()]

conn.close()

# Normalise datetimes (sqlite3.Row returns strings, but just in case)
for c in customers:
    if c.get("created_at") and hasattr(c["created_at"], "isoformat"):
        c["created_at"] = c["created_at"].isoformat()

export = {"customers": customers, "vehicles": vehicles}
OUT_PATH.write_text(json.dumps(export, indent=2, default=str))

print(f"Exported {len(customers)} customers and {len(vehicles)} vehicles")
print(f"Output: {OUT_PATH}")

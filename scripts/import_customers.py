"""
Import customers + authorized_vehicles into production PostgreSQL.
Run against the live DB after Railway deployment:

  DATABASE_URL=<railway-postgres-url> python scripts/import_customers.py

Idempotent: skips any customer_id or vehicle_id that already exists.
"""

import json
import os
from pathlib import Path

# Must be installed: psycopg2-binary
import psycopg2
import psycopg2.extras

EXPORT_PATH = Path(__file__).parent / "customer_export.json"

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("Set DATABASE_URL environment variable before running this script.")

# Railway sometimes gives postgres:// — psycopg2 needs postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

data = json.loads(EXPORT_PATH.read_text())
customers = data["customers"]
vehicles  = data["vehicles"]

conn = psycopg2.connect(database_url)
cur  = conn.cursor()

imported_customers = 0
skipped_customers  = 0
imported_vehicles  = 0
skipped_vehicles   = 0

for c in customers:
    cur.execute("SELECT 1 FROM customers WHERE customer_id = %s", (c["customer_id"],))
    if cur.fetchone():
        skipped_customers += 1
        continue

    cur.execute("""
        INSERT INTO customers
            (customer_id, company_name, gst_number, fleet_manager_name,
             whatsapp_number, credit_limit, payment_terms_days,
             outstanding_balance, is_active, created_at, notes)
        VALUES
            (%(customer_id)s, %(company_name)s, %(gst_number)s, %(fleet_manager_name)s,
             %(whatsapp_number)s, %(credit_limit)s, %(payment_terms_days)s,
             %(outstanding_balance)s, %(is_active)s, %(created_at)s, %(notes)s)
    """, c)
    imported_customers += 1

for v in vehicles:
    cur.execute("SELECT 1 FROM authorized_vehicles WHERE vehicle_id = %s", (v["vehicle_id"],))
    if cur.fetchone():
        skipped_vehicles += 1
        continue

    cur.execute("""
        INSERT INTO authorized_vehicles
            (vehicle_id, customer_id, vehicle_number, vehicle_description, is_active)
        VALUES
            (%(vehicle_id)s, %(customer_id)s, %(vehicle_number)s,
             %(vehicle_description)s, %(is_active)s)
    """, v)
    imported_vehicles += 1

# Reset sequences so future inserts don't collide with migrated IDs
cur.execute("SELECT setval('customers_customer_id_seq', (SELECT MAX(customer_id) FROM customers))")
cur.execute("SELECT setval('authorized_vehicles_vehicle_id_seq', (SELECT MAX(vehicle_id) FROM authorized_vehicles))")

conn.commit()
cur.close()
conn.close()

print(f"Customers: {imported_customers} imported, {skipped_customers} skipped")
print(f"Vehicles:  {imported_vehicles} imported,  {skipped_vehicles} skipped")
print("Done.")

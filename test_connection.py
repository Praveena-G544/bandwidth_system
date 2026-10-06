import psycopg2
from config import DB
try:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usage_records")
    print("Connected OK. usage_records rows:", cur.fetchone()[0])
except Exception as e:
    print("FAILED:", e)
    print("Check: password in config.py, database name, and that 1_schema.sql + 2_seed_data.sql were run.")

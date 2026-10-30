"""Apply idempotent staging seed for NW-LOCAL-ORG-001 to the staging PostgreSQL database."""
import os
from pathlib import Path

try:
    import psycopg
    connect_fn = psycopg.connect
except ImportError:
    import psycopg2
    connect_fn = psycopg2.connect

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


def seed_staging(host=None, port=None, dbname=None, user=None, password=None):
    host = host or os.getenv("DB_HOST", "127.0.0.1")
    port = int(port or os.getenv("DB_PORT", "5434"))
    dbname = dbname or os.getenv("DB_NAME", "nextwireless_staging")
    user = user or os.getenv("DB_USER", "nw_staging_user")
    password = password or os.getenv("DB_PASSWORD")

    if not password:
        raise ValueError("DB_PASSWORD must be provided to seed the staging database")

    seed_path = os.getenv("STAGING_SEED_PATH")
    if seed_path and Path(seed_path).exists():
        seed_file = Path(seed_path)
    elif (REPO_ROOT / "db" / "staging_seed" / "099_staging_seed.sql").exists():
        seed_file = REPO_ROOT / "db" / "staging_seed" / "099_staging_seed.sql"
    elif Path("/db/staging_seed/099_staging_seed.sql").exists():
        seed_file = Path("/db/staging_seed/099_staging_seed.sql")
    else:
        raise FileNotFoundError("099_staging_seed.sql not found in expected locations")

    sql = seed_file.read_text(encoding="utf-8")
    conn = connect_fn(host=host, port=port, dbname=dbname, user=user, password=password)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"Staging seed for NW-LOCAL-ORG-001 applied successfully to {dbname}@{host}:{port}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed_staging()

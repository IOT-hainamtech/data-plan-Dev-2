"""Apply additive Rule Engine SQL to an existing PostgreSQL test database.

On a NEW volume, Compose init scripts already create these tables automatically.
"""
from pathlib import Path
import os
import sqlparse
from ingestor import db_connection


def main():
    names = ("002_rules.sql", "003_m1_rule_engine.sql", "004_durable_outbox.sql",
             "005_urd_alignment.sql", "006_quality_contract.sql",
             "007_status_tracker_timeout.sql")
    scripts = [Path(__file__).with_name(name) for name in names]
    scripts.append(Path(os.getenv("MULTI_SITE_MIGRATION", "/migrations/008_multi_site_registry.sql")))
    statements = []
    for script in scripts:
        statements.extend(stmt.strip() for stmt in sqlparse.split(script.read_text(encoding="utf-8")) if stmt.strip())
    conn = db_connection()
    try:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
        print(f"Rule migrations OK: {len(statements)} statements executed")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

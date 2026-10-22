"""Apply canonical additive migrations to an existing PostgreSQL database.

On a NEW volume, Compose init scripts already create these tables automatically.
"""
import os
from pathlib import Path
import sqlparse
from core.database import db_connection


def main():
    migration_dir = Path(os.getenv("MIGRATIONS_DIR", "/migrations"))
    scripts = [path for path in sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
               if not path.name.startswith("001_")]
    if not scripts:
        raise RuntimeError(f"No additive migrations found in {migration_dir}")
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

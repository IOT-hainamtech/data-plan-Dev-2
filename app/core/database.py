"""PostgreSQL connection factory shared by application entrypoints."""
import os

import psycopg


def db_connection():
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "postgres"), port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "nextwireless"), user=os.environ.get("DB_USER", "nw_user"),
        password=os.environ["DB_PASSWORD"], connect_timeout=5, autocommit=False)

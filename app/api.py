"""Read-only local Data API prototype; NO AUTH/TLS, bind only to localhost.

Not the finalized IF-01..IF-07 contract; do not expose to the internet.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from datetime import datetime, timezone

from ingestor import db_connection

app = FastAPI(title="nextWireless L2A Data Plane (local PoC)", version="0.1.0")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@app.get("/api/v1/health")
def health():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"service": "nextwireless-data-api", "status": "UP"}
    except Exception:
        return JSONResponse(status_code=503, content={"service": "nextwireless-data-api", "status": "DOWN"})


@app.get("/api/v1/outbox/metrics")
def outbox_metrics():
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT
                count(*) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT')),
                count(*) FILTER (WHERE delivery_state='FAILED'),
                COALESCE(sum(failure_count),0),
                min(created_at) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT'))
                FROM alarm_outbox""")
            pending, failed, failures, oldest = cur.fetchone()
    age = max(0.0, (utc_now() - oldest).total_seconds()) if oldest else 0.0
    return {"pending": pending, "failed": failed, "delivery_failures": failures,
            "oldest_pending_age_seconds": round(age, 3)}


@app.get("/api/v1/devices/{device_id}/latest")
def latest(device_id: str):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND enabled=TRUE", (device_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT DISTINCT ON (metric) metric, value_number, measured_at
                FROM telemetry WHERE device_id=%s ORDER BY metric, measured_at DESC""", (device_id,))
            rows = cur.fetchall()
    return {"device_id": device_id, "metrics": [
        {"metric": r["metric"], "value": r["value_number"],
         "measured_at": r["measured_at"].isoformat() + "Z"} for r in rows]}


@app.get("/api/v1/devices/{device_id}/telemetry")
def telemetry(device_id: str, metric: str | None = None,
              limit: int = Query(default=100, ge=1, le=1000)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND enabled=TRUE", (device_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT metric, value_number, measured_at FROM telemetry
                WHERE device_id=%s AND (%s::text IS NULL OR metric=%s)
                ORDER BY measured_at DESC LIMIT %s""", (device_id, metric, metric, limit))
            rows = cur.fetchall()
    return {"device_id": device_id, "points": [
        {"metric": r["metric"], "value": r["value_number"],
         "measured_at": r["measured_at"].isoformat() + "Z"} for r in rows]}

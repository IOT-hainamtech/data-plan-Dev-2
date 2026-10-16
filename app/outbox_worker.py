"""Durable alarm Outbox Worker.

The HTTP contract in this module is a mock contract, not the finalized nextGRP API.
Delivery is disabled unless OUTBOX_DELIVERY_ENABLED=true.
"""
import json
import logging
import os
import random
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ingestor import db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("outbox-worker")


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def retry_delay(attempt, base_seconds, max_seconds, jitter_ratio, rng=random.random):
    raw = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter = raw * jitter_ratio * ((rng() * 2) - 1)
    return max(0.0, raw + jitter)


def validate_ack(body, alarm_key):
    return (isinstance(body, dict) and body.get("accepted") is True
            and body.get("alarm_key") == alarm_key
            and isinstance(body.get("ack_id"), str) and bool(body["ack_id"]))


@dataclass(frozen=True)
class DeliveryResult:
    status: int
    body: dict


class NextGrpAdapter:
    """Adapter for the provisional mock ACK contract; replace after API sign-off."""

    def __init__(self, url, timeout, auth_mode="bearer", token=None,
                 api_key_header="X-API-Key", allow_insecure_mock=False):
        if not url.startswith("https://") and not allow_insecure_mock:
            raise ValueError("NEXTGRP_ALARM_URL must use HTTPS")
        if auth_mode not in ("bearer", "api_key", "none"):
            raise ValueError("unsupported NEXTGRP_AUTH_MODE")
        if auth_mode != "none" and not token:
            raise ValueError("NEXTGRP_AUTH_TOKEN is required")
        self.url, self.timeout, self.auth_mode = url, timeout, auth_mode
        self.token, self.api_key_header = token, api_key_header

    def send(self, alarm_key, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Idempotency-Key": alarm_key}
        if self.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.auth_mode == "api_key":
            headers[self.api_key_header] = self.token
        request = urllib.request.Request(
            self.url, data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers, method="POST")
        context = ssl.create_default_context() if self.url.startswith("https://") else None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        return DeliveryResult(status=status, body=body)


class OutboxStore:
    def __init__(self, connection_factory=db_connection, alarm_key=None):
        self.connection_factory = connection_factory
        self.alarm_key = alarm_key

    def claim(self, worker_id, batch_size, lease_seconds, at):
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("""WITH candidates AS (
                    SELECT id FROM alarm_outbox
                    WHERE ((delivery_state='PENDING' AND next_attempt_at <= %s)
                       OR (delivery_state='IN_FLIGHT' AND locked_until <= %s))
                    """ + (" AND alarm_key=%s" if self.alarm_key else "") + """
                    ORDER BY next_attempt_at,id FOR UPDATE SKIP LOCKED LIMIT %s
                ) UPDATE alarm_outbox o SET delivery_state='IN_FLIGHT',locked_by=%s,
                    locked_until=%s,attempt_count=o.attempt_count+1,last_error=NULL
                  FROM candidates c WHERE o.id=c.id
                  RETURNING o.id,o.alarm_key,o.payload_json,o.attempt_count""",
                    ((at, at, self.alarm_key, batch_size, worker_id,
                      at + timedelta(seconds=lease_seconds)) if self.alarm_key else
                     (at, at, batch_size, worker_id, at + timedelta(seconds=lease_seconds))))
                rows = cur.fetchall()
            conn.commit()
        return rows

    def delivered(self, row_id, worker_id, ack_id, status, at):
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE alarm_outbox SET delivery_state='DELIVERED',
                    delivered_at=%s,acked_at=%s,ack_id=%s,last_http_status=%s,
                    locked_by=NULL,locked_until=NULL,last_error=NULL
                    WHERE id=%s AND delivery_state='IN_FLIGHT' AND locked_by=%s""",
                    (at, at, ack_id, status, row_id, worker_id))
                changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def failed(self, row_id, worker_id, attempt, error, status, at,
               max_attempts, base_seconds, max_seconds, jitter_ratio, rng=random.random):
        terminal = attempt >= max_attempts
        delay = retry_delay(attempt, base_seconds, max_seconds, jitter_ratio, rng)
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE alarm_outbox SET delivery_state=%s,failure_count=failure_count+1,
                    next_attempt_at=%s,last_error=%s,last_http_status=%s,
                    locked_by=NULL,locked_until=NULL
                    WHERE id=%s AND delivery_state='IN_FLIGHT' AND locked_by=%s""",
                    ("FAILED" if terminal else "PENDING", at + timedelta(seconds=delay),
                     str(error)[:500], status, row_id, worker_id))
                changed = cur.rowcount
            conn.commit()
        return bool(changed), terminal, delay

    def metrics(self, at):
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT
                    count(*) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT')),
                    count(*) FILTER (WHERE delivery_state='FAILED'),
                    COALESCE(sum(failure_count),0),
                    min(created_at) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT'))
                    FROM alarm_outbox""")
                pending, failed, failures, oldest = cur.fetchone()
        age = max(0.0, (at - oldest).total_seconds()) if oldest else 0.0
        return {"pending": pending, "failed": failed, "delivery_failures": failures,
                "oldest_pending_age_seconds": round(age, 3)}


class OutboxWorker:
    def __init__(self, store, adapter, *, worker_id=None, batch_size=20, lease_seconds=60,
                 max_attempts=8, base_seconds=2, max_seconds=300, jitter_ratio=0.2):
        self.store, self.adapter = store, adapter
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:10]}"
        self.batch_size, self.lease_seconds = batch_size, lease_seconds
        self.max_attempts, self.base_seconds = max_attempts, base_seconds
        self.max_seconds, self.jitter_ratio = max_seconds, jitter_ratio

    def run_once(self, at=None):
        at = at or now_utc()
        rows = self.store.claim(self.worker_id, self.batch_size, self.lease_seconds, at)
        for row_id, alarm_key, payload, attempt in rows:
            status = None
            try:
                result = self.adapter.send(alarm_key, payload)
                status = result.status
                if not (200 <= status < 300) or not validate_ack(result.body, alarm_key):
                    raise ValueError(f"invalid ACK (HTTP {status})")
                self.store.delivered(row_id, self.worker_id, result.body["ack_id"], status, now_utc())
                LOG.info("ACK alarm_key=%s attempt=%d ack_id=%s", alarm_key, attempt,
                         result.body["ack_id"])
            except Exception as exc:
                changed, terminal, delay = self.store.failed(
                    row_id, self.worker_id, attempt, exc, status, now_utc(), self.max_attempts,
                    self.base_seconds, self.max_seconds, self.jitter_ratio)
                if changed:
                    LOG.warning("DELIVERY_FAILED alarm_key=%s attempt=%d terminal=%s retry_in=%.2fs error=%s",
                                alarm_key, attempt, terminal, delay, exc)
        return len(rows)


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


def main():
    if not _env_bool("OUTBOX_DELIVERY_ENABLED"):
        raise SystemExit("OUTBOX_DELIVERY_ENABLED is false; refusing external delivery")
    adapter = NextGrpAdapter(
        os.environ["NEXTGRP_ALARM_URL"], float(os.getenv("NEXTGRP_TIMEOUT_SECONDS", "5")),
        os.getenv("NEXTGRP_AUTH_MODE", "bearer"), os.getenv("NEXTGRP_AUTH_TOKEN"),
        os.getenv("NEXTGRP_API_KEY_HEADER", "X-API-Key"),
        _env_bool("NEXTGRP_ALLOW_INSECURE_MOCK"))
    worker = OutboxWorker(
        OutboxStore(), adapter, batch_size=int(os.getenv("OUTBOX_BATCH_SIZE", "20")),
        lease_seconds=int(os.getenv("OUTBOX_LEASE_SECONDS", "60")),
        max_attempts=int(os.getenv("OUTBOX_MAX_ATTEMPTS", "8")),
        base_seconds=float(os.getenv("OUTBOX_BACKOFF_BASE_SECONDS", "2")),
        max_seconds=float(os.getenv("OUTBOX_BACKOFF_MAX_SECONDS", "300")),
        jitter_ratio=float(os.getenv("OUTBOX_BACKOFF_JITTER", "0.2")))
    poll = max(0.1, float(os.getenv("OUTBOX_POLL_SECONDS", "1")))
    metrics_interval = max(1.0, float(os.getenv("OUTBOX_METRICS_SECONDS", "30")))
    next_metrics = 0.0
    while True:
        try:
            processed = worker.run_once()
            monotonic = time.monotonic()
            if monotonic >= next_metrics:
                LOG.info("OUTBOX_METRICS %s", json.dumps(worker.store.metrics(now_utc())))
                next_metrics = monotonic + metrics_interval
            if not processed:
                time.sleep(poll)
        except Exception:
            LOG.exception("Worker iteration failed")
            time.sleep(poll)


if __name__ == "__main__":
    main()

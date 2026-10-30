"""Durable alarm Outbox Worker.

The HTTP contract in this module is a mock contract, not the finalized nextGRP API.
Delivery is disabled unless OUTBOX_DELIVERY_ENABLED=true.
"""
import json
import logging
import os
import random
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

from core.clock import now_utc
from core.database import db_connection
from psycopg.types.json import Jsonb
from integrations.nextgrp import DeliveryResult, FrappeAlarmAdapter, NextGrpAdapter
from integrations.dev1_config import map_alarm_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("outbox-worker")


def retry_delay(attempt, base_seconds, max_seconds, jitter_ratio, rng=random.random):
    raw = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter = raw * jitter_ratio * ((rng() * 2) - 1)
    return max(0.0, raw + jitter)


def validate_ack(body, alarm_key, event_id=None):
    return (isinstance(body, dict) and body.get("accepted") is True
            and body.get("alarm_key") == alarm_key
            and (event_id is None or body.get("event_id") == event_id)
            and isinstance(body.get("ack_id"), str) and bool(body["ack_id"]))


class OutboxStore:
    def __init__(self, connection_factory=db_connection, alarm_key=None,
                 table="alarm_outbox", key_column="alarm_key", not_before=None):
        self.connection_factory = connection_factory
        self.alarm_key = alarm_key
        self.table, self.key_column = table, key_column
        self.not_before = not_before

    def claim(self, worker_id, batch_size, lease_seconds, at):
        filters = ""
        params = [at, at]
        if self.not_before is not None:
            filters += " AND created_at >= %s"
            params.append(self.not_before)
        if self.alarm_key:
            filters += f" AND {self.key_column}=%s"
            params.append(self.alarm_key)
        ordering_guard = ""
        if self.table == "alarm_outbox":
            ordering_guard = """AND NOT (payload_json->>'kind'='cleared' AND EXISTS (
                        SELECT 1 FROM alarm_outbox prior
                        WHERE prior.wire_alarm_key=alarm_outbox.wire_alarm_key
                          AND prior.payload_json->>'kind'='occurrence'
                          AND prior.delivery_state<>'DELIVERED'))"""
        params.extend((batch_size, worker_id, at + timedelta(seconds=lease_seconds)))
        wire_return = ",o.wire_alarm_key" if self.table == "alarm_outbox" else ""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""WITH candidates AS (
                    SELECT id FROM {self.table}
                    WHERE ((delivery_state='PENDING' AND next_attempt_at <= %s)
                       OR (delivery_state='IN_FLIGHT' AND locked_until <= %s))
                    {ordering_guard}
                    {filters}
                    ORDER BY next_attempt_at,id FOR UPDATE SKIP LOCKED LIMIT %s
                ) UPDATE {self.table} o SET delivery_state='IN_FLIGHT',locked_by=%s,
                    locked_until=%s,attempt_count=o.attempt_count+1,last_error=NULL
                  FROM candidates c WHERE o.id=c.id
                  RETURNING o.id,o.{self.key_column},o.payload_json,o.attempt_count{wire_return}""",
                    tuple(params))
                rows = cur.fetchall()
            conn.commit()
        return rows

    def delivered(self, row_id, worker_id, ack_id, status, at):
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""UPDATE {self.table} SET delivery_state='DELIVERED',
                    delivered_at=%s,acked_at=%s,ack_id=%s,last_http_status=%s,
                    locked_by=NULL,locked_until=NULL,last_error=NULL
                    WHERE id=%s AND delivery_state='IN_FLIGHT' AND locked_by=%s""",
                    (at, at, ack_id, status, row_id, worker_id))
                changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def persist_payload(self, row_id, worker_id, payload):
        """Freeze the wire payload before the first network attempt."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""UPDATE {self.table} SET payload_json=%s
                    WHERE id=%s AND delivery_state='IN_FLIGHT' AND locked_by=%s""",
                    (Jsonb(payload), row_id, worker_id))
                changed = cur.rowcount
            conn.commit()
        return bool(changed)

    def failed(self, row_id, worker_id, attempt, error, status, at,
               max_attempts, base_seconds, max_seconds, jitter_ratio, rng=random.random,
               force_terminal=False):
        terminal = force_terminal or (max_attempts is not None and attempt >= max_attempts)
        delay = retry_delay(attempt, base_seconds, max_seconds, jitter_ratio, rng)
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""UPDATE {self.table} SET delivery_state=%s,failure_count=failure_count+1,
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
                cur.execute(f"""SELECT
                    count(*) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT')),
                    count(*) FILTER (WHERE delivery_state='FAILED'),
                    COALESCE(sum(failure_count),0),
                    min(created_at) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT'))
                    FROM {self.table}""")
                pending, failed, failures, oldest = cur.fetchone()
        age = max(0.0, (at - oldest).total_seconds()) if oldest else 0.0
        return {"pending": pending, "failed": failed, "delivery_failures": failures,
                "oldest_pending_age_seconds": round(age, 3)}


class OutboxWorker:
    def __init__(self, store, adapter, *, worker_id=None, batch_size=20, lease_seconds=60,
                 max_attempts=None, base_seconds=1, max_seconds=60, jitter_ratio=0.2,
                 payload_transform=None):
        self.store, self.adapter = store, adapter
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:10]}"
        self.batch_size, self.lease_seconds = batch_size, lease_seconds
        self.max_attempts, self.base_seconds = max_attempts, base_seconds
        self.max_seconds, self.jitter_ratio = max_seconds, jitter_ratio
        self.payload_transform = payload_transform

    def run_once(self, at=None):
        at = at or now_utc()
        rows = self.store.claim(self.worker_id, self.batch_size, self.lease_seconds, at)
        for row in rows:
            row_id, alarm_key, payload, attempt = row[:4]
            wire_alarm_key = row[4] if len(row) > 4 else None
            status = None
            try:
                already_frozen = (wire_alarm_key is not None and isinstance(payload, dict)
                                  and payload.get("alarm_key") != wire_alarm_key)
                if self.payload_transform and not already_frozen:
                    payload = self.payload_transform(payload)
                    if not self.store.persist_payload(row_id, self.worker_id, payload):
                        raise RuntimeError("unable to freeze transformed Outbox payload")
                result = self.adapter.send(alarm_key, payload)
                status = result.status
                event_id=payload.get("event_id") if isinstance(payload,dict) else None
                expected_alarm=payload.get("alarm_key",str(alarm_key)) if isinstance(payload,dict) else str(alarm_key)
                status_ok = status == 202 if getattr(self.adapter,"require_202",False) else 200 <= status < 300
                ack_validator = getattr(self.adapter, "validate_ack", validate_ack)
                if not status_ok or not ack_validator(result.body, expected_alarm, event_id):
                    raise ValueError(f"invalid ACK (HTTP {status})")
                receipt = (self.adapter.delivery_receipt(result.body, expected_alarm, event_id)
                           if hasattr(self.adapter, "delivery_receipt") else result.body["ack_id"])
                if not receipt:
                    raise ValueError("delivery response has no stable receipt")
                self.store.delivered(row_id, self.worker_id, receipt, status, now_utc())
                LOG.info("ACK alarm_key=%s attempt=%d ack_id=%s", alarm_key, attempt,
                         receipt)
            except Exception as exc:
                changed, terminal, delay = self.store.failed(
                    row_id, self.worker_id, attempt, exc, status, now_utc(), self.max_attempts,
                    self.base_seconds, self.max_seconds, self.jitter_ratio,
                    force_terminal=(status is not None and 400 <= status < 500
                                    and status not in (408, 429)))
                if changed:
                    LOG.warning("DELIVERY_FAILED alarm_key=%s attempt=%d terminal=%s retry_in=%.2fs error=%s",
                                alarm_key, attempt, terminal, delay, exc)
        return len(rows)


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


def bridge_settings():
    mode = os.getenv("BRIDGE_MODE", "mock").strip().lower()
    if mode not in ("mock", "real"):
        raise ValueError("BRIDGE_MODE must be mock or real")
    alarm_url = os.getenv("BRIDGE_ALARM_URL", os.getenv("NEXTGRP_ALARM_URL", ""))
    status_url = os.getenv("BRIDGE_STATUS_URL", alarm_url)
    token = os.getenv("BRIDGE_OUTBOUND_TOKEN") or os.getenv("NEXTGRP_AUTH_TOKEN")
    if not alarm_url or not status_url:
        raise ValueError("BRIDGE_ALARM_URL and BRIDGE_STATUS_URL are required")
    if mode == "real":
        if "mock-nextgrp" in alarm_url or "mock-nextgrp" in status_url:
            raise ValueError("BRIDGE_MODE=real refuses Mock Bridge URLs")
        if not token or token == "mock-development-token":
            raise ValueError("BRIDGE_MODE=real requires BRIDGE_OUTBOUND_TOKEN")
    return mode, alarm_url, status_url, token


def alarm_adapter(mode, alarm_url, token, timeout, allow_http):
    protocol = os.getenv("BRIDGE_ALARM_PROTOCOL", "mock").strip().lower()
    if protocol == "mock":
        return NextGrpAdapter(alarm_url, timeout, "bearer", token,
            os.getenv("NEXTGRP_API_KEY_HEADER", "X-API-Key"), allow_http,
            require_202=_env_bool("BRIDGE_REQUIRE_202", True))
    if protocol == "frappe":
        if mode != "real":
            raise ValueError("BRIDGE_ALARM_PROTOCOL=frappe requires BRIDGE_MODE=real")
        return FrappeAlarmAdapter(alarm_url, timeout, token,
            os.getenv("FRAPPE_SITE_NAME", ""), allow_insecure=allow_http)
    raise ValueError("BRIDGE_ALARM_PROTOCOL must be mock or frappe")


def delivery_not_before():
    value = os.getenv("BRIDGE_DELIVER_NOT_BEFORE", "").strip()
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("BRIDGE_DELIVER_NOT_BEFORE must include a timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def main():
    if not _env_bool("OUTBOX_DELIVERY_ENABLED"):
        raise SystemExit("OUTBOX_DELIVERY_ENABLED is false; refusing external delivery")
    mode, alarm_url, status_url, token = bridge_settings()
    not_before = delivery_not_before()
    allow_http = _env_bool("BRIDGE_ALLOW_INSECURE_HTTP",
                           _env_bool("NEXTGRP_ALLOW_INSECURE_MOCK"))
    adapter = alarm_adapter(mode, alarm_url, token,
        float(os.getenv("BRIDGE_TIMEOUT_SECONDS", "5")), allow_http)
    payload_transform = None
    if isinstance(adapter, FrappeAlarmAdapter):
        def payload_transform(payload):
            with db_connection() as conn:
                with conn.cursor() as cur:
                    return map_alarm_payload(cur, payload)
    worker = OutboxWorker(
        OutboxStore(not_before=not_before), adapter, batch_size=int(os.getenv("OUTBOX_BATCH_SIZE", "20")),
        lease_seconds=int(os.getenv("OUTBOX_LEASE_SECONDS", "60")),
        max_attempts=None,
        base_seconds=float(os.getenv("OUTBOX_BACKOFF_BASE_SECONDS", "1")),
        max_seconds=float(os.getenv("OUTBOX_BACKOFF_MAX_SECONDS", "60")),
        jitter_ratio=float(os.getenv("OUTBOX_BACKOFF_JITTER", "0.2")),
        payload_transform=payload_transform)
    status_adapter = NextGrpAdapter(
        status_url, float(os.getenv("BRIDGE_TIMEOUT_SECONDS","5")),"bearer",token,
        allow_insecure_mock=allow_http,require_202=_env_bool("BRIDGE_REQUIRE_202",True))
    status_worker = OutboxWorker(OutboxStore(table="status_outbox",key_column="delivery_event_id",
        not_before=not_before),status_adapter,
        batch_size=int(os.getenv("OUTBOX_BATCH_SIZE","20")),lease_seconds=int(os.getenv("OUTBOX_LEASE_SECONDS","60")),
        max_attempts=None,base_seconds=float(os.getenv("STATUS_OUTBOX_BACKOFF_BASE_SECONDS","1")),
        max_seconds=float(os.getenv("STATUS_OUTBOX_BACKOFF_MAX_SECONDS","60")),
        jitter_ratio=float(os.getenv("STATUS_OUTBOX_BACKOFF_JITTER","0.2")))
    if isinstance(adapter, FrappeAlarmAdapter):
        # Phase 1 signs off Alarm only. Never send the incompatible Mock status
        # contract merely because the Alarm transport was switched to Frappe.
        status_worker = None
    poll = max(0.1, float(os.getenv("OUTBOX_POLL_SECONDS", "1")))
    metrics_interval = max(1.0, float(os.getenv("OUTBOX_METRICS_SECONDS", "30")))
    next_metrics = 0.0
    LOG.info("BRIDGE_MODE=%s alarm_host=%s status_host=%s delivery_not_before=%s", mode,
             adapter.url.split("/", 3)[2], status_adapter.url.split("/", 3)[2],
             not_before.isoformat() + "Z" if not_before else "unset")
    while True:
        try:
            processed = worker.run_once()
            if status_worker is not None:
                processed += status_worker.run_once()
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

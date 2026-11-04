"""Opt-in, high-water guarded delivery of new device events and status transitions."""
import logging
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.clock import now_utc
from core.database import db_connection
from integrations.frappe_client import FrappeClient, FrappeHTTPError
from integrations.nextgrp import DeliveryResult
from outbox.worker import OutboxStore, OutboxWorker

LOG = logging.getLogger("realtime-worker")
VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _bool(name):
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def _csv(name):
    result = tuple(sorted({x.strip() for x in os.getenv(name, "").split(",") if x.strip()}))
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def armed_selector():
    if not _bool("REALTIME_E2E_ARMED"):
        raise ValueError("REALTIME_E2E_ARMED=true is required")
    try:
        event_mark = int(os.environ["REALTIME_EVENT_HIGH_WATER_ID"])
        status_mark = int(os.environ["REALTIME_STATUS_HIGH_WATER_ID"])
    except (KeyError, ValueError) as exc:
        raise ValueError("both realtime high-water IDs are required integers") from exc
    raw = os.getenv("REALTIME_ARMED_AT", "")
    try:
        armed_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("REALTIME_ARMED_AT must be ISO-8601") from exc
    if armed_at.tzinfo is None or event_mark < 0 or status_mark < 0:
        raise ValueError("ARM timestamp needs timezone and high-water IDs must be non-negative")
    org, devices = os.getenv("REALTIME_ORG", ""), _csv("REALTIME_DEVICES")
    if org != "hainam" or set(devices) != {"TH-001", "DR-001"}:
        raise ValueError("realtime scope must be hainam and exactly TH-001,DR-001")
    return {"event_mark": event_mark, "status_mark": status_mark,
            "armed_at": armed_at.astimezone(timezone.utc).replace(tzinfo=None),
            "org": org, "devices": devices}


def _mapping(cur, device_id):
    cur.execute("""SELECT external_id FROM integration_identity_mappings
        WHERE system_code='nextgrp' AND entity_type='DEVICE' AND internal_id=%s
          AND confirmed=TRUE AND enabled=TRUE""", (device_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"confirmed Device mapping missing for {device_id}")
    return row[0]


def map_event_payload(payload):
    with db_connection() as conn:
        with conn.cursor() as cur:
            external = _mapping(cur, payload["device_id"])
    mapped = {k: v for k, v in payload.items() if k != "org_code"}
    mapped["_data_plane_device_id"] = payload["device_id"]
    mapped["device_id"] = external
    stamp = datetime.fromisoformat(mapped["event_at"].replace("Z", "+00:00"))
    mapped["event_at"] = stamp.astimezone(VN).isoformat()
    for metric in mapped.get("metrics", []):
        metric["unit"] = {"Cel": "\u00b0C", "%RH": "%"}.get(
            metric.get("unit"), metric.get("unit"))
    mapped["_wire_frozen"] = True
    return mapped


def map_status_payload(payload):
    with db_connection() as conn:
        with conn.cursor() as cur:
            external = _mapping(cur, payload["device_id"])
    return {"device_id": external, "state": payload["state"],
            "_data_plane_device_id": payload["device_id"], "_wire_frozen": True}


class RealtimeAdapter:
    expects_payload_ack = True
    require_202 = False

    def __init__(self, client, kind):
        self.client, self.kind = client, kind

    @staticmethod
    def _wire(payload):
        return {k: v for k, v in payload.items() if not k.startswith("_")}

    def send(self, key, payload):
        try:
            response = (self.client.post_device_event(self._wire(payload)) if self.kind == "event"
                        else self.client.post_status_json(self._wire(payload)))
            return DeliveryResult(response.status, response.envelope)
        except FrappeHTTPError as exc:
            return DeliveryResult(exc.status, exc.envelope)

    def validate_ack(self, body, expected, event_id=None):
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, dict) or not isinstance(expected, dict):
            return False
        if self.kind == "event":
            return (message.get("accepted") is True
                    and message.get("device_id") == expected.get("device_id")
                    and message.get("data_plane_device_id") == expected.get("_data_plane_device_id")
                    and message.get("event_id") == expected.get("event_id")
                    and message.get("event_type") == expected.get("event_type"))
        online_state = message.get("online_state")
        previous_state = message.get("previous_state")
        return (message.get("device_id") == expected.get("device_id")
                and isinstance(online_state, str)
                and online_state.lower() == expected.get("state")
                and isinstance(message.get("changed"), bool)
                and isinstance(message.get("last_seen"), str)
                and (previous_state is None or
                     (isinstance(previous_state, str)
                      and previous_state.lower() in ("online", "offline"))))

    def delivery_receipt(self, body, key, event_id=None):
        message = body["message"]
        return (message.get("event_id") if self.kind == "event"
                else f"{message['device_id']}:{message['online_state']}:{message['last_seen']}")


def build_workers(selector):
    base = os.environ["DEV1_BASE_URL"]
    client = FrappeClient(base, os.getenv("FRAPPE_SITE_NAME", "nextgrp.localhost"),
        api_key=os.getenv("DEV1_API_KEY"), api_secret=os.getenv("DEV1_API_SECRET"),
        org_code=selector["org"], timeout=float(os.getenv("REALTIME_HTTP_TIMEOUT", "5")))
    common = {"not_before": selector["armed_at"], "approved_org": selector["org"],
              "approved_devices": selector["devices"]}
    events = OutboxWorker(OutboxStore(table="device_event_outbox", key_column="event_id",
        min_id_exclusive=selector["event_mark"], **common), RealtimeAdapter(client, "event"),
        payload_transform=map_event_payload, max_attempts=None)
    statuses = OutboxWorker(OutboxStore(table="status_outbox", key_column="delivery_event_id",
        min_id_exclusive=selector["status_mark"], **common), RealtimeAdapter(client, "status"),
        payload_transform=map_status_payload, max_attempts=None)
    return events, statuses


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    selector = armed_selector()
    events, statuses = build_workers(selector)
    LOG.warning("REALTIME_ARMED event_id>%d status_id>%d armed_at=%s scope=%s/%s",
        selector["event_mark"], selector["status_mark"], selector["armed_at"],
        selector["org"], ",".join(selector["devices"]))
    poll = max(0.1, float(os.getenv("REALTIME_POLL_SECONDS", "0.5")))
    while True:
        processed = events.run_once() + statuses.run_once()
        if not processed:
            time.sleep(poll)


if __name__ == "__main__":
    main()

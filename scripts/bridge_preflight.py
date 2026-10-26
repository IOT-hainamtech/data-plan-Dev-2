"""Non-destructive Real Bridge preflight with redacted output.

Connectivity-only mode sends no event. Full mode creates uniquely identified demo
status/alarm events and requires the v1.3 HTTP 202 ACK contract.
"""
import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def request(url, method="GET", payload=None, token=None, event_id=None, timeout=5):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if event_id:
        headers["Idempotency-Key"] = event_id
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"detail": "non-JSON response"}
        return exc.code, parsed


def valid_ack(status, body, event_id, alarm_key=None):
    return (status == 202 and isinstance(body, dict) and body.get("accepted") is True
            and body.get("event_id") == event_id and bool(body.get("ack_id"))
            and (alarm_key is None or body.get("alarm_key") == alarm_key))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("BRIDGE_BASE_URL", "http://192.168.1.83:8081"))
    parser.add_argument("--connectivity-only", action="store_true")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SystemExit("FAIL invalid Bridge URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=args.timeout):
            pass
        print(f"PASS tcp host={parsed.hostname} port={port}")
    except OSError as exc:
        print(f"FAIL tcp error={type(exc).__name__}")
        return 1

    try:
        health_status, _ = request(base + "/health", timeout=args.timeout)
        health_ok = 200 <= health_status < 300
        print(f"{'PASS' if health_ok else 'FAIL'} health http={health_status}")
    except (OSError, urllib.error.URLError) as exc:
        print(f"FAIL health error={type(exc).__name__}")
        health_ok = False
    if args.connectivity_only:
        print("SKIP auth/schema/ack (connectivity-only; no event sent)")
        return 0 if health_ok else 1

    token = os.getenv("BRIDGE_OUTBOUND_TOKEN")
    if not token and os.path.isfile(args.env_file):
        with open(args.env_file, encoding="utf-8-sig") as handle:
            for line in handle:
                key, separator, value = line.strip().partition("=")
                if separator and key == "BRIDGE_OUTBOUND_TOKEN":
                    token = value.strip().strip('"').strip("'")
                    break
    if not token or token == "CHANGE_ME_WHEN_DEV1_PROVIDES_A_TOKEN":
        print("FAIL auth BRIDGE_OUTBOUND_TOKEN=MISSING (secret not displayed)")
        return 2
    print("PASS secret BRIDGE_OUTBOUND_TOKEN=SET (redacted)")
    suffix = uuid.uuid4().hex
    now = stamp()
    status_event = {"event_id": f"preflight-status-{suffix}", "org_code": "demo",
                    "device_id": "BRIDGE-PREFLIGHT", "state": "online", "since": now,
                    "last_seen": now, "reason": "telemetry"}
    status, body = request(base + "/internal/events/status", "POST", status_event, token,
                           status_event["event_id"], args.timeout)
    status_ok = valid_ack(status, body, status_event["event_id"])
    print(f"{'PASS' if status_ok else 'FAIL'} status http={status} ack={'valid' if status_ok else 'invalid'}")

    alarm_key = f"PREFLIGHT:BRIDGE-PREFLIGHT:{suffix}"
    alarm = {"event_id": f"preflight-alarm-{suffix}", "org_code": "demo",
             "alarm_key": alarm_key, "episode_key": alarm_key, "kind": "occurrence",
             "rule_id": "BRIDGE_PREFLIGHT", "rule_version": 1,
             "device_id": "BRIDGE-PREFLIGHT", "zone_code": "preflight",
             "severity": "Low", "episode_start": now, "occurrence_seq": 1,
             "raised_at": now, "event": {"event_id": f"preflight-source-{suffix}",
             "event_type": "BRIDGE_PREFLIGHT", "ts": now,
             "data": {"summary": "Controlled Dev 2 Bridge preflight"}}}
    first = request(base + "/internal/events/alarm", "POST", alarm, token,
                    alarm["event_id"], args.timeout)
    duplicate = request(base + "/internal/events/alarm", "POST", alarm, token,
                        alarm["event_id"], args.timeout)
    first_ok = valid_ack(*first, alarm["event_id"], alarm_key)
    duplicate_ok = (valid_ack(*duplicate, alarm["event_id"], alarm_key)
                    and duplicate[1].get("ack_id") == first[1].get("ack_id"))
    print(f"{'PASS' if first_ok else 'FAIL'} alarm http={first[0]} ack={'valid' if first_ok else 'invalid'}")
    print(f"{'PASS' if duplicate_ok else 'FAIL'} duplicate http={duplicate[0]} ack={'stable' if duplicate_ok else 'invalid'}")
    return 0 if health_ok and status_ok and first_ok and duplicate_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, urllib.error.URLError) as exc:
        print(f"FAIL network error={type(exc).__name__}")
        sys.exit(1)

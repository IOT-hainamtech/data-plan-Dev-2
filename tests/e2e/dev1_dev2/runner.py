"""Re-runnable Phase-1 runner. POST mode is deliberately double opt-in."""
import argparse
import json
import os
from pathlib import Path

from integrations.frappe_client import FrappeClient, FrappeHTTPError
from integrations.nextgrp import FrappeAlarmAdapter
from tests.e2e.dev1_dev2.contract import (validate_alarm_ack,
                                         validate_alarm_payload,
                                         validate_auth_headers,
                                         validate_config,
                                         validate_frappe_envelope,
                                         validate_status_payload)

HERE = Path(__file__).resolve().parent
TEST_DEVICES = ("NW-IT-TH001", "NW-IT-DR001")


def fixtures():
    return [json.loads((HERE / "fixtures" / name).read_text(encoding="utf-8"))
            for name in ("s1.json", "s6.json")]


def offline_report():
    results = []
    # Test auth header & envelope offline validation
    sample_headers = {
        "Authorization": "token TEST_KEY:TEST_SECRET",
        "X-Frappe-Site-Name": "nextgrp.localhost",
        "Accept": "application/json"
    }
    validate_auth_headers(sample_headers, site_name="nextgrp.localhost")
    results.append(_result("auth", "headers (token KEY:SECRET)", "PASS"))

    sample_envelope = {"message": {"status": "ok"}}
    validate_frappe_envelope(sample_envelope)
    results.append(_result("envelope", "frappe response.message", "PASS"))

    sample_ack = {
        "alarm_key": "test:key:1",
        "kind": "occurrence",
        "status": "New",
        "occurrence_count": 1,
        "changed": True,
        "duplicate": False
    }
    validate_alarm_ack(sample_ack, expected_alarm_key="test:key:1", is_duplicate=False)
    results.append(_result("alarm_ack", "occurrence ACK validation", "PASS"))

    sample_dup_ack = {
        "alarm_key": "test:key:1",
        "occurrence_count": 1,
        "changed": False,
        "duplicate": True
    }
    validate_alarm_ack(sample_dup_ack, expected_alarm_key="test:key:1", is_duplicate=True)
    results.append(_result("alarm_ack", "duplicate ACK validation", "PASS"))

    for fixture in fixtures():
        device = fixture["device_id"]
        for name, validator in (
            ("config", lambda value: validate_config(value, device)),
            ("status", lambda value: validate_status_payload(value, device)),
            ("alarm_occurrence", lambda value: validate_alarm_payload(value, device)),
            ("alarm_cleared", lambda value: validate_alarm_payload(value, device)),
        ):
            validator(fixture[name])
            results.append(_result(device, name, "PASS"))
    results.extend([
        _result("IF-01/02/03", "push/full snapshot", "BLOCKED", "Dev 1 not complete"),
        _result("IF-04", "status ACK contract", "PARTIAL", "transport known; ACK schema unconfirmed; fail-closed"),
        _result("IF-06", "lifecycle callback", "BLOCKED", "Dev 1 callback not complete"),
        _result("commands", "claim/ack body contract", "PARTIAL", "endpoints known; claim/ack not confirmed for demo S1/S6"),
    ])
    return results


def live_client():
    token = os.getenv("DEV1_SERVICE_TOKEN")
    api_key = os.getenv("DEV1_API_KEY") or os.getenv("M1_FRAPPE_API_KEY")
    api_secret = os.getenv("DEV1_API_SECRET") or os.getenv("M1_FRAPPE_API_SECRET")
    if not token and not (api_key and api_secret):
        raise RuntimeError("DEV1_SERVICE_TOKEN (or DEV1_API_KEY:DEV1_API_SECRET) is required in the process environment")
    return FrappeClient(
        os.getenv("DEV1_BASE_URL", "http://192.168.1.43:8000"),
        os.getenv("DEV1_SITE_NAME", "nextgrp.localhost"),
        token=token, api_key=api_key, api_secret=api_secret,
        timeout=float(os.getenv("DEV1_TIMEOUT_SECONDS", "5")),
        allow_http_lan=os.getenv("DEV1_ALLOW_HTTP_LAN", "false").lower() == "true")


def readonly_report():
    try:
        client = live_client()
    except Exception as exc:
        return [_result("environment", "GET config", "SKIP", str(exc))]
    results = []
    for device in TEST_DEVICES:
        try:
            response = client.get_device_config(device)
            validate_config(response.message, device)
            results.append(_result(device, "GET config", "PASS"))
        except FrappeHTTPError as exc:
            results.append(_result(device, "GET config", "FAIL", f"HTTP {exc.status}"))
        except Exception as exc:
            results.append(_result(device, "GET config", "FAIL", type(exc).__name__))
    return results


def post_report():
    if os.getenv("DEV1_E2E_ALLOW_POST") != "NW-IT-ONLY":
        return [_result("staging", "POST mode opt-in", "BLOCKED",
                        "set DEV1_E2E_ALLOW_POST=NW-IT-ONLY for explicit staging POST opt-in")]
    approved = {value.strip() for value in os.getenv("DEV1_E2E_APPROVED_DEVICES", "").split(",") if value.strip()}
    if approved != set(TEST_DEVICES):
        return [_result("staging", "POST approved devices", "BLOCKED",
                        "DEV1_E2E_APPROVED_DEVICES must contain exactly NW-IT-TH001,NW-IT-DR001")]
    try:
        client = live_client()
    except Exception as exc:
        return [_result("staging", "POST client setup", "BLOCKED", str(exc))]
    results = []
    for fixture in fixtures():
        device = fixture["device_id"]
        validate_status_payload(fixture["status"], device)
        # IF-04 body and ACK are not signed off, so no request is emitted.
        results.append(_result(device, "POST status", "SKIP",
                               "request/ACK schema unconfirmed; fail-closed"))
        occurrence = validate_alarm_payload(fixture["alarm_occurrence"], device)
        first = client.post_alarm(occurrence)
        duplicate = client.post_alarm(occurrence)
        clear_payload = validate_alarm_payload(fixture["alarm_cleared"], device)
        cleared = client.post_alarm(clear_payload)
        results.extend([
            _alarm_result(device, "POST alarm occurrence", first, occurrence),
            _alarm_result(device, "POST alarm duplicate", duplicate, occurrence),
            _alarm_result(device, "POST alarm cleared", cleared, clear_payload),
        ])
    return results


def _result(subject, test, status, detail=""):
    return {"subject": subject, "test": test, "status": status, "detail": detail}


def _alarm_result(device, test, response, payload):
    valid = FrappeAlarmAdapter.validate_ack(response.envelope, payload["alarm_key"],
                                            payload["event_id"])
    return _result(device, test, "PASS" if valid else "FAIL",
                   f"HTTP {response.status}; Frappe message ACK {'valid' if valid else 'invalid'}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "read-only", "post"), default="offline")
    args = parser.parse_args(argv)
    report = {"mode": args.mode, "results": {
        "offline": offline_report, "read-only": readonly_report, "post": post_report}[args.mode]()}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if any(row["status"] == "FAIL" for row in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

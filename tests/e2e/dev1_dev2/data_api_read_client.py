"""Read-only Dev 1 -> Dev 2 verification using the approved identity mapping.

This client never accepts an arbitrary tenant mapping from a request. The fixed
mapping is checked before any HTTP call and credentials are read from the process
environment without being printed.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


IDENTITIES = {
    "organization": ("NW-LOCAL-ORG-001", "hainam"),
    "site": ("NW-LOCAL-SITE-001", "hq"),
    "devices": {"NW-IT-TH001": "TH-001", "NW-IT-DR001": "DR-001"},
}


def _validate_base_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("DATA_API_URL must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
        if os.getenv("DATA_API_ALLOW_HTTP_LAN", "false").lower() != "true":
            raise ValueError("HTTP LAN requires DATA_API_ALLOW_HTTP_LAN=true")
    return value.rstrip("/")


def read_latest(base_url, token, external_device, opener=urllib.request.urlopen):
    internal_device = IDENTITIES["devices"].get(external_device)
    if not internal_device:
        raise ValueError("device is outside the approved integration mapping")
    request = urllib.request.Request(
        f"{_validate_base_url(base_url)}/api/v1/devices/{internal_device}/latest",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Org-Code": IDENTITIES["organization"][1],
            "Accept": "application/json",
        },
    )
    try:
        with opener(request, timeout=8, context=ssl.create_default_context()) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                "Data API rejected the credential: use its dedicated Bearer token, "
                "not the Frappe 'token API_KEY:API_SECRET' credential"
            ) from exc
        raise
    if body.get("device_id") != internal_device or body.get("organization") != "hainam":
        raise RuntimeError("Data API response identity does not match approved mapping")
    snapshot = body.get("latest_snapshot") or {}
    return {
        "organization": IDENTITIES["organization"][0],
        "site": IDENTITIES["site"][0],
        "device_id": external_device,
        "source_identity": {
            "organization": body["organization"], "device_id": body["device_id"]},
        "online_status": body.get("online_status"),
        "data_status": body.get("data_status"),
        "seq": body.get("seq"),
        "measured_at_utc": body.get("measured_at_utc") or snapshot.get("measured_at_utc"),
        "received_at_utc": body.get("received_at_utc") or snapshot.get("received_at_utc"),
        "metrics": body.get("metrics", []),
        "door_state": body.get("door_state"),
    }


def main():
    token = os.getenv("DP_INBOUND_TOKEN")
    if not token:
        raise SystemExit("DP_INBOUND_TOKEN is required and must not be passed on the command line")
    base_url = os.getenv("DATA_API_URL", "http://127.0.0.1:8002")
    try:
        report = [read_latest(base_url, token, device) for device in IDENTITIES["devices"]]
    except Exception as exc:
        print(f"DATA_API_READ=FAIL reason={exc}", file=sys.stderr)
        return 1
    print(json.dumps({"result": "PASS", "devices": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

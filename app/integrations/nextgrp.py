"""Provisional nextGRP alarm adapter; replace only after contract sign-off."""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryResult:
    status: int
    body: dict


class NextGrpAdapter:
    def __init__(self, url, timeout, auth_mode="bearer", token=None,
                 api_key_header="X-API-Key", allow_insecure_mock=False, require_202=False):
        if not url.startswith("https://") and not allow_insecure_mock:
            raise ValueError("NEXTGRP_ALARM_URL must use HTTPS")
        if auth_mode not in ("bearer", "api_key", "none"):
            raise ValueError("unsupported NEXTGRP_AUTH_MODE")
        if auth_mode != "none" and not token:
            raise ValueError("NEXTGRP_AUTH_TOKEN is required")
        self.url, self.timeout, self.auth_mode = url, timeout, auth_mode
        self.token, self.api_key_header = token, api_key_header
        self.require_202 = require_202

    def send(self, alarm_key, payload):
        event_id = payload.get("event_id", alarm_key)
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Idempotency-Key": event_id}
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
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        return DeliveryResult(status=status, body=body)


class FrappeAlarmAdapter:
    """Alarm transport for the signed-off Frappe method contract.

    This is deliberately separate from ``NextGrpAdapter``: the latter speaks the
    development Mock Bridge JSON/ACK protocol.
    """

    require_202 = False

    def __init__(self, url, timeout, token=None, site_name=None, *,
                 api_key=None, api_secret=None, allow_insecure=False,
                 opener=urllib.request.urlopen):
        if not url.startswith("https://") and not allow_insecure:
            raise ValueError("Frappe alarm URL must use HTTPS")
        from integrations.frappe_client import validate_and_normalize_credentials
        self.api_key, self.api_secret, self.token = validate_and_normalize_credentials(
            token, api_key=api_key, api_secret=api_secret)
        if not site_name or any(c in site_name for c in "\r\n"):
            raise ValueError("Frappe site name is required")
        self.url, self.timeout = url, timeout
        self.site_name, self.opener = site_name, opener

    def __repr__(self):
        return f"<FrappeAlarmAdapter url={self.url!r} site={self.site_name!r}>"

    def send(self, alarm_key, payload):
        encoded_json = json.dumps(payload, separators=(",", ":"))
        body = urllib.parse.urlencode({"payload": encoded_json}).encode("ascii")
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"token {self.token}",
            "X-Frappe-Site-Name": self.site_name,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        context = ssl.create_default_context() if self.url.startswith("https://") else None
        try:
            with self.opener(request, timeout=self.timeout, context=context) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            parsed = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        return DeliveryResult(status=status, body=parsed)

    @staticmethod
    def validate_ack(body, alarm_key, event_id=None):
        """Validate Frappe's ``message`` response, never the Mock ACK shape."""
        if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
            return False
        message = body["message"]
        if message.get("alarm_key") != alarm_key:
            return False
        duplicate = message.get("duplicate")
        if duplicate is True:
            return message.get("changed") is False
        return (duplicate is False and message.get("changed") is True
                and isinstance(message.get("status"), str)
                and isinstance(message.get("occurrence_count"), int))

    @staticmethod
    def delivery_receipt(body, alarm_key, event_id=None):
        # Dev 1 has no ack_id. Persist the stable outbound event_id as correlation receipt.
        return event_id

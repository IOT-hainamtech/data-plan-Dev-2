"""Provisional nextGRP alarm adapter; replace only after contract sign-off."""
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryResult:
    status: int
    body: dict


class NextGrpAdapter:
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
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        return DeliveryResult(status=status, body=body)

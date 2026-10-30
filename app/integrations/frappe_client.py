"""Small fail-closed client for the confirmed nextGRP/Frappe v15 methods.

This module contains transport only. It never logs credentials, retries POSTs,
or treats an unconfirmed response shape as a business acknowledgement.
"""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


METHOD_PREFIX = "/api/method/nextgrp.nextgrp_iot.controller."


def validate_and_normalize_credentials(token=None, *, api_key=None, api_secret=None):
    """Validate and normalize Frappe credentials to (api_key, api_secret, 'KEY:SECRET').

    Both key and secret are required. Credentials missing either part fail closed.
    Never exposes raw secrets in error messages.
    """
    if api_key is not None or api_secret is not None:
        if not api_key or not isinstance(api_key, str) or api_key.strip() != api_key:
            raise ValueError("Dev 1 API key must be a non-empty string without whitespace")
        if any(c in api_key for c in "\r\n:"):
            raise ValueError("Dev 1 API key contains invalid characters")
        if not api_secret or not isinstance(api_secret, str) or api_secret.strip() != api_secret:
            raise ValueError("Dev 1 API secret must be a non-empty string without whitespace")
        if any(c in api_secret for c in "\r\n"):
            raise ValueError("Dev 1 API secret contains invalid characters")
        return api_key, api_secret, f"{api_key}:{api_secret}"

    if not token or not isinstance(token, str) or token.strip() != token:
        raise ValueError("Dev 1 API credentials are required (API_KEY:API_SECRET)")
    if any(c in token for c in "\r\n"):
        raise ValueError("Dev 1 API credentials contain invalid characters")
    if ":" not in token:
        raise ValueError("Dev 1 API credentials must contain both API_KEY and API_SECRET separated by ':'")
    parts = token.split(":", 1)
    k, s = parts[0].strip(), parts[1].strip()
    if not k or not s or any(c in k for c in "\r\n:") or any(c in s for c in "\r\n"):
        raise ValueError("Dev 1 API credentials must contain non-empty API_KEY and API_SECRET")
    return k, s, f"{k}:{s}"


@dataclass(frozen=True)
class FrappeResponse:
    status: int
    message: object
    envelope: dict


class FrappeHTTPError(RuntimeError):
    def __init__(self, status, envelope=None):
        self.status = int(status)
        self.envelope = envelope if isinstance(envelope, dict) else {}
        self.retryable = self.status in (408, 429) or self.status >= 500
        super().__init__(f"Dev 1 request failed with HTTP {self.status}")


class FrappeClient:
    METHODS = {
        "config": "config_controller.get_device_config",
        "status": "status_controller.receive_status",
        "alarm": "alarm_controller.receive_alarm",
        "claim_command": "command_controller.claim_command",
        "ack_command": "command_controller.ack_command",
    }

    def __init__(self, base_url, site_name, token=None, timeout=5, *,
                 api_key=None, api_secret=None, allow_http_lan=False,
                 opener=urllib.request.urlopen):
        base_url = base_url.rstrip("/")
        if not base_url.startswith("https://") and not allow_http_lan:
            raise ValueError("Dev 1 requires HTTPS unless test-only HTTP LAN opt-in is enabled")
        self.api_key, self.api_secret, self.token = validate_and_normalize_credentials(
            token, api_key=api_key, api_secret=api_secret)
        if not site_name or any(c in site_name for c in "\r\n"):
            raise ValueError("Frappe site name is required")
        self.base_url, self.site_name = base_url, site_name
        self.timeout, self.opener = float(timeout), opener

    def __repr__(self):
        return f"<FrappeClient base_url={self.base_url!r} site={self.site_name!r}>"

    def method_url(self, name):
        try:
            method = self.METHODS[name]
        except KeyError as exc:
            raise ValueError("unknown Dev 1 method") from exc
        return self.base_url + METHOD_PREFIX + method

    def _request(self, name, *, query=None, form=None):
        url = self.method_url(name)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if form is None else urllib.parse.urlencode(form).encode("utf-8")
        headers = {
            "Authorization": f"token {self.token}",
            "X-Frappe-Site-Name": self.site_name,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="GET" if data is None else "POST")
        context = ssl.create_default_context() if url.startswith("https://") else None
        status = None
        raw = b""
        try:
            with self.opener(request, timeout=self.timeout, context=context) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()

        envelope = None
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if status is not None and (status < 200 or status >= 300):
                raise FrappeHTTPError(status, {"error": "Non-JSON response from server"})
            raise ValueError("Dev 1 returned invalid JSON") from exc

        if not isinstance(envelope, dict):
            raise ValueError("Dev 1 response must be a JSON object")
        if status < 200 or status >= 300:
            raise FrappeHTTPError(status, envelope)
        if "message" not in envelope:
            raise ValueError("Dev 1 response is missing message")
        return FrappeResponse(status, envelope["message"], envelope)

    def get_device_config(self, device_id):
        if not device_id or any(c in device_id for c in "\r\n"):
            raise ValueError("device_id is required")
        return self._request("config", query={"device_id": device_id})

    def post_status(self, device_id_or_payload, state=None):
        if isinstance(device_id_or_payload, dict):
            device_id = device_id_or_payload.get("device_id")
            state = device_id_or_payload.get("state")
        else:
            device_id = device_id_or_payload
        if not device_id or any(c in str(device_id) for c in "\r\n"):
            raise ValueError("device_id is required")
        if state not in ("online", "offline"):
            raise ValueError("state must be 'online' or 'offline'")
        return self._request("status", form={"device_id": device_id, "state": state})

    def post_alarm(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        for req in ("event_id", "org_code", "device_id", "alarm_key", "kind"):
            if not payload.get(req):
                raise ValueError(f"alarm payload missing required field '{req}'")
        if payload.get("kind") not in ("occurrence", "cleared"):
            raise ValueError("alarm kind must be 'occurrence' or 'cleared'")
        return self._request("alarm", form={"payload": _json_payload(payload)})

    def claim_command(self, device_id):
        if not device_id or any(c in str(device_id) for c in "\r\n"):
            raise ValueError("device_id is required")
        return self._request("claim_command", form={"device_id": device_id})

    def ack_command(self, command_id, delivery_token, status, result_json=None):
        if not command_id or any(c in str(command_id) for c in "\r\n"):
            raise ValueError("command_id is required")
        if not delivery_token or any(c in str(delivery_token) for c in "\r\n"):
            raise ValueError("delivery_token is required")
        if status not in ("Succeeded", "Failed"):
            raise ValueError("status must be 'Succeeded' or 'Failed'")
        form = {
            "command_id": command_id,
            "delivery_token": delivery_token,
            "status": status,
        }
        if result_json is not None:
            if isinstance(result_json, (dict, list)):
                form["result_json"] = json.dumps(result_json, separators=(",", ":"), ensure_ascii=False)
            elif isinstance(result_json, str):
                form["result_json"] = result_json
            else:
                raise ValueError("result_json must be a JSON string or dict/list")
        return self._request("ack_command", form=form)


def _json_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

"""Development-only mock of the provisional nextGRP Alarm ACK contract."""
import os
import time
import uuid
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Mock nextGRP Alarm API", version="mock-1")
_lock = Lock()
_acks = {}


def _authorized(authorization, api_key):
    token = os.getenv("MOCK_NEXTGRP_TOKEN", "mock-development-token")
    return authorization == f"Bearer {token}" or api_key == token


@app.get("/health")
def health():
    return {"status": "UP", "contract": "PROVISIONAL_MOCK"}


@app.post("/mock/v1/alarms")
async def alarm(request: Request, idempotency_key: str = Header(alias="Idempotency-Key"),
                authorization: str | None = Header(default=None),
                x_api_key: str | None = Header(default=None, alias="X-API-Key"),
                x_mock_mode: str | None = Header(default=None, alias="X-Mock-Mode")):
    if not _authorized(authorization, x_api_key):
        raise HTTPException(status_code=401, detail="invalid mock credentials")
    payload = await request.json()
    if payload.get("alarm_key") != idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency key mismatch")
    if x_mock_mode == "error":
        raise HTTPException(status_code=503, detail="simulated unavailable")
    if x_mock_mode == "timeout":
        time.sleep(float(os.getenv("MOCK_TIMEOUT_SECONDS", "2")))
    if x_mock_mode == "invalid_ack":
        return {"accepted": True, "alarm_key": "wrong", "ack_id": "invalid"}
    with _lock:
        duplicate = idempotency_key in _acks
        ack_id = _acks.setdefault(idempotency_key, f"mock-{uuid.uuid4().hex}")
    return {"accepted": True, "duplicate": duplicate,
            "alarm_key": idempotency_key, "ack_id": ack_id}


@app.get("/mock/v1/metrics")
def metrics():
    with _lock:
        return {"unique_alarms": len(_acks)}

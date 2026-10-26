"""Development-only mock of the provisional nextGRP Alarm ACK contract."""
import os
import hashlib
import json
import time
import uuid
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request, Response
from psycopg.types.json import Jsonb

from core.database import db_connection
from api.models import BridgeAlarmIn, BridgeStatusIn

app = FastAPI(title="Mock nextGRP Alarm API", version="mock-1")
_lock = Lock()
_acks = {}
_snapshot_default = {"contract_version":"1.3-proposed","resources":[]}


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
    if idempotency_key not in (payload.get("alarm_key"), payload.get("event_id")):
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
    return {"accepted": True, "duplicate": duplicate, "event_id": payload.get("event_id"),
            "alarm_key": payload.get("alarm_key"), "ack_id": ack_id}


@app.get("/mock/v1/metrics")
def metrics():
    with _lock:
        return {"unique_alarms": len(_acks)}

async def _bridge_event(request, idempotency_key, authorization, kind, response, validated):
    if not _authorized(authorization,None): raise HTTPException(401,"invalid mock credentials")
    payload=validated.model_dump(mode="json", exclude_none=True)
    if payload.get("event_id")!=idempotency_key: raise HTTPException(400,"event_id/idempotency mismatch")
    mode=request.headers.get("X-Mock-Mode")
    if mode=="error": raise HTTPException(503,"simulated unavailable")
    if mode=="timeout": time.sleep(float(os.getenv("MOCK_TIMEOUT_SECONDS","2")))
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload_hash,ack_id FROM bridge_event_inbox WHERE event_id=%s FOR UPDATE",(idempotency_key,))
            existing=cur.fetchone()
            if existing and existing[0] != digest: raise HTTPException(409,"EVENT_ID_CONFLICT")
            duplicate=existing is not None
            if existing: ack_id=str(existing[1])
            else:
                ack_id=str(uuid.uuid4())
                cur.execute("INSERT INTO bridge_event_inbox(event_id,event_kind,payload_hash,payload_json,ack_id) VALUES(%s,%s,%s,%s,%s)",
                            (idempotency_key,kind,digest,Jsonb(payload),ack_id))
        conn.commit()
    response.status_code=202
    return {"accepted":True,"duplicate":duplicate,"event_id":idempotency_key,
            "alarm_key":payload.get("alarm_key"),"ack_id":ack_id}

@app.post("/internal/events/alarm")
async def bridge_alarm(payload:BridgeAlarmIn,request:Request,response:Response,idempotency_key:str=Header(alias="Idempotency-Key"),authorization:str|None=Header(default=None)):
    return await _bridge_event(request,idempotency_key,authorization,"alarm",response,payload)

@app.post("/internal/events/status")
async def bridge_status(payload:BridgeStatusIn,request:Request,response:Response,idempotency_key:str=Header(alias="Idempotency-Key"),authorization:str|None=Header(default=None)):
    result=await _bridge_event(request,idempotency_key,authorization,"status",response,payload)
    result["alarm_key"]=idempotency_key
    return result

@app.get("/internal/snapshot")
def snapshot(authorization:str|None=Header(default=None)):
    if not _authorized(authorization,None): raise HTTPException(401,"invalid mock credentials")
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT snapshot_json FROM bridge_snapshot_store WHERE singleton=TRUE")
            row=cur.fetchone()
    return row[0] if row else _snapshot_default

@app.put("/mock/v1/snapshot")
async def set_snapshot(request:Request,authorization:str|None=Header(default=None)):
    if not _authorized(authorization,None): raise HTTPException(401,"invalid mock credentials")
    value=await request.json()
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO bridge_snapshot_store(singleton,snapshot_json) VALUES(TRUE,%s) ON CONFLICT(singleton) DO UPDATE SET snapshot_json=EXCLUDED.snapshot_json,updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'",(Jsonb(value),))
        conn.commit()
    return {"stored":True}

@app.get("/mock/v1/inbox")
def inbox():
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT event_kind,payload_json,ack_id FROM bridge_event_inbox ORDER BY created_at,event_id")
            rows=cur.fetchall()
    return {"events":[{"kind":kind,"payload":payload,"ack_id":str(ack_id)} for kind,payload,ack_id in rows]}

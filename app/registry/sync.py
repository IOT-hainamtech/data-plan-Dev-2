"""Transactional, version-monotonic Registry/Rule synchronization."""
import hashlib
import json

from fastapi import HTTPException
from psycopg.types.json import Jsonb


def payload_hash(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def version_decision(cur, table, key_sql, key_args, version, digest):
    cur.execute(f"SELECT config_version,payload_hash FROM {table} WHERE {key_sql} FOR UPDATE", key_args)
    row = cur.fetchone()
    if not row:
        return "apply"
    current, current_hash = row
    if version < current:
        raise HTTPException(status_code=409, detail={"code": "STALE_VERSION", "applied_version": current})
    if version == current:
        if current_hash == digest:
            return "duplicate"
        raise HTTPException(status_code=409, detail={"code": "VERSION_PAYLOAD_CONFLICT", "applied_version": current})
    return "apply"


def sync_response(resource_id, version, duplicate=False):
    return {"status": "duplicate" if duplicate else "applied", "resource_id": resource_id,
            "applied_version": version, "duplicate": duplicate}


def apply_organization(cur, data):
    digest = payload_hash(data)
    decision = version_decision(cur, "organizations", "org_code=%s", (data["org_code"],), data["version"], digest)
    if decision == "duplicate": return sync_response(data["org_code"], data["version"], True)
    cur.execute("""INSERT INTO organizations(org_code,display_name,enabled,metadata_json,config_version,payload_hash)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(org_code) DO UPDATE SET
        display_name=EXCLUDED.display_name,enabled=EXCLUDED.enabled,metadata_json=EXCLUDED.metadata_json,
        config_version=EXCLUDED.config_version,payload_hash=EXCLUDED.payload_hash""",
        (data["org_code"],data["display_name"],data["enabled"],Jsonb(data["metadata"]),data["version"],digest))
    return sync_response(data["org_code"], data["version"])


def apply_site(cur, data):
    digest=payload_hash(data); key=(data["org_code"],data["site_code"])
    if version_decision(cur,"sites","org_code=%s AND site_code=%s",key,data["version"],digest)=="duplicate": return sync_response(data["site_code"],data["version"],True)
    cur.execute("SELECT 1 FROM organizations WHERE org_code=%s AND enabled=TRUE",(data["org_code"],))
    if not cur.fetchone(): raise HTTPException(422,"Organization does not exist or is disabled")
    cur.execute("""INSERT INTO sites(org_code,site_code,display_name,timezone,enabled,metadata_json,config_version,payload_hash)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(org_code,site_code) DO UPDATE SET display_name=EXCLUDED.display_name,
      timezone=EXCLUDED.timezone,enabled=EXCLUDED.enabled,metadata_json=EXCLUDED.metadata_json,config_version=EXCLUDED.config_version,payload_hash=EXCLUDED.payload_hash""",
      (*key,data["display_name"],data["timezone"],data["enabled"],Jsonb(data["metadata"]),data["version"],digest))
    return sync_response(data["site_code"],data["version"])


def apply_zone(cur,data):
    digest=payload_hash(data); key=(data["org_code"],data["site_code"],data["zone_code"])
    if version_decision(cur,"zones","org_code=%s AND site_code=%s AND zone_code=%s",key,data["version"],digest)=="duplicate": return sync_response(data["zone_code"],data["version"],True)
    cur.execute("SELECT 1 FROM sites WHERE org_code=%s AND site_code=%s AND enabled=TRUE",key[:2])
    if not cur.fetchone(): raise HTTPException(422,"Site does not exist or is disabled")
    cur.execute("""INSERT INTO zones(org_code,site_code,zone_code,display_name,enabled,metadata_json,config_version,payload_hash)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(org_code,site_code,zone_code) DO UPDATE SET display_name=EXCLUDED.display_name,
      enabled=EXCLUDED.enabled,metadata_json=EXCLUDED.metadata_json,config_version=EXCLUDED.config_version,payload_hash=EXCLUDED.payload_hash""",
      (*key,data["display_name"],data["enabled"],Jsonb(data["metadata"]),data["version"],digest))
    return sync_response(data["zone_code"],data["version"])


def apply_profile(cur, data):
    digest=payload_hash(data); code=data["profile_code"]
    if version_decision(cur,"device_profiles","profile_code=%s",(code,),data["version"],digest)=="duplicate": return sync_response(code,data["version"],True)
    cur.execute("""INSERT INTO device_profiles(profile_code,display_name,enabled,metadata_json,config_version,payload_hash)
      VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(profile_code) DO UPDATE SET display_name=EXCLUDED.display_name,
      enabled=EXCLUDED.enabled,metadata_json=EXCLUDED.metadata_json,config_version=EXCLUDED.config_version,payload_hash=EXCLUDED.payload_hash""",
      (code,data["display_name"],data["enabled"],Jsonb(data["metadata"]),data["version"],digest))
    cur.execute("DELETE FROM device_profile_points WHERE profile_code=%s",(code,))
    for p in data["data_points"]:
        # required applies only when quality=good; ingestion intentionally permits data={} for quality=error.
        cur.execute("""INSERT INTO device_profile_points(profile_code,channel,point_code,data_type,unit,min_value,max_value,
          allowed_values,required,precision_digits,metadata_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (code,p["channel"],p["code"],p["data_type"],p.get("unit"),p.get("min_value"),p.get("max_value"),
           Jsonb(p.get("allowed_values")) if p.get("allowed_values") is not None else None,p["required"],p.get("precision"),Jsonb(p.get("metadata",{}))))
    cur.execute("DELETE FROM device_profile_events WHERE profile_code=%s",(code,))
    for e in data["event_types"]:
        cur.execute("INSERT INTO device_profile_events(profile_code,event_type,state_point,state_value) VALUES(%s,%s,%s,%s)",
                    (code,e["event_type"],e.get("state_point"),e.get("state_value")))
    return sync_response(code,data["version"])


def apply_device(cur,data):
    digest=payload_hash(data); device=data["device_id"]
    if version_decision(cur,"devices","device_id=%s",(device,),data["version"],digest)=="duplicate": return sync_response(device,data["version"],True)
    cur.execute("SELECT 1 FROM zones WHERE org_code=%s AND site_code=%s AND zone_code=%s AND enabled=TRUE",
                (data["org_code"],data["site_code"],data["zone_code"]))
    if not cur.fetchone(): raise HTTPException(422,"Zone does not exist or is disabled")
    cur.execute("SELECT metadata_json FROM device_profiles WHERE profile_code=%s AND enabled=TRUE",(data["profile_code"],))
    profile=cur.fetchone()
    if not profile: raise HTTPException(422,"Profile does not exist or is disabled")
    profile_metadata=profile[0] or {}
    telemetry_interval=profile_metadata.get("expected_interval_s",data["telemetry_interval_seconds"])
    heartbeat_interval=profile_metadata.get("heartbeat_interval_s",data["heartbeat_interval_seconds"])
    cur.execute("""INSERT INTO devices(device_id,org_code,site_code,zone_code,profile_code,enabled,
      telemetry_interval_seconds,heartbeat_interval_seconds,metadata_json,credential_ref,connectivity,config_version,payload_hash)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(device_id) DO UPDATE SET
      org_code=EXCLUDED.org_code,site_code=EXCLUDED.site_code,zone_code=EXCLUDED.zone_code,
      profile_code=EXCLUDED.profile_code,enabled=EXCLUDED.enabled,telemetry_interval_seconds=EXCLUDED.telemetry_interval_seconds,
      heartbeat_interval_seconds=EXCLUDED.heartbeat_interval_seconds,metadata_json=EXCLUDED.metadata_json,
      credential_ref=EXCLUDED.credential_ref,connectivity=EXCLUDED.connectivity,config_version=EXCLUDED.config_version,payload_hash=EXCLUDED.payload_hash""",
      (device,data["org_code"],data["site_code"],data["zone_code"],data["profile_code"],data["enabled"],
       telemetry_interval,heartbeat_interval,Jsonb(data["metadata"]),
       data.get("credential_ref"),data["connectivity"],data["version"],digest))
    return sync_response(device,data["version"])


def apply_rule(cur,data):
    digest=payload_hash(data); code=data["rule_id"]; scope=data["scope"]
    if version_decision(cur,"rule_definitions","rule_code=%s",(code,),data["version"],digest)=="duplicate": return sync_response(code,data["version"],True)
    org=data["org_code"]
    st=scope["type"]
    device=scope.get("device_id") if st=="DEVICE" else None
    if device:
        cur.execute("SELECT 1 FROM devices WHERE device_id=%s AND org_code=%s",(device,org))
        if not cur.fetchone(): raise HTTPException(422,"Rule device is outside organization")
    elif scope.get("org_code") and scope["org_code"] != org:
        raise HTTPException(422,"Rule scope organization mismatch")
    severity={"Low":"INFO","Medium":"WARNING","High":"HIGH","Critical":"CRITICAL",
              "LOW":"INFO","MEDIUM":"WARNING"}.get(data["severity"],data["severity"].upper())
    cfg=data
    cur.execute("""INSERT INTO rule_definitions(rule_code,device_id,rule_kind,metric,event_type,threshold_value,
      hysteresis_value,hold_seconds,clear_hold_seconds,quiet_seconds,severity,enabled,business_start,business_end,
      schedule_timezone,scope_type,scope_org_code,scope_site_code,scope_zone_code,scope_profile_code,
      config_version,payload_hash,config_json)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(rule_code) DO UPDATE SET device_id=EXCLUDED.device_id,rule_kind=EXCLUDED.rule_kind,
      metric=EXCLUDED.metric,event_type=EXCLUDED.event_type,threshold_value=EXCLUDED.threshold_value,
      hysteresis_value=EXCLUDED.hysteresis_value,hold_seconds=EXCLUDED.hold_seconds,
      clear_hold_seconds=EXCLUDED.clear_hold_seconds,quiet_seconds=EXCLUDED.quiet_seconds,severity=EXCLUDED.severity,
      enabled=EXCLUDED.enabled,business_start=EXCLUDED.business_start,business_end=EXCLUDED.business_end,
      schedule_timezone=EXCLUDED.schedule_timezone,scope_type=EXCLUDED.scope_type,scope_org_code=EXCLUDED.scope_org_code,
      scope_site_code=EXCLUDED.scope_site_code,scope_zone_code=EXCLUDED.scope_zone_code,
      scope_profile_code=EXCLUDED.scope_profile_code,config_version=EXCLUDED.config_version,
      payload_hash=EXCLUDED.payload_hash,config_json=EXCLUDED.config_json,updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'""",
      (code,device,data["rule_kind"],data.get("metric"),data.get("event_type"),data.get("threshold"),
       data.get("hysteresis",0),(data.get("open_limit_s") if data.get("open_limit_s") is not None else data.get("duration_s",0)),data.get("clear_duration_s",0),
       data.get("quiet_window_s",0),severity,data["enabled"],data.get("business_start"),data.get("business_end"),
       data.get("timezone"),st,org if st in ("ORGANIZATION","SITE","ZONE") else scope.get("org_code"),
       scope.get("site_code"),scope.get("zone_code"),scope.get("profile_code"),data["version"],digest,Jsonb(cfg)))
    return sync_response(code,data["version"])

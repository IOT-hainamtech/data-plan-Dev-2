"""Durable M1 S1/S6 rule state machines using the caller's DB transaction."""
import hashlib
import json
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def _alarm_key(rule_code, device_id, source_key, occurrence_kind="TRIGGERED"):
    return f"m1:{rule_code}:{_key(rule_code, device_id, source_key, occurrence_kind)}"


def _freeze_rule_snapshot(cur, rule_code, device_id):
    """Capture immutable Rule configuration and confirmed integration identity."""
    cur.execute("""SELECT config_version,payload_hash,config_json,severity,
            threshold_value,hysteresis_value,hold_seconds,clear_hold_seconds,
            quiet_seconds,business_start,business_end,schedule_timezone
        FROM rule_definitions WHERE rule_code=%s""", (rule_code,))
    row=cur.fetchone()
    if not row:
        raise ValueError("cannot freeze a missing Rule")
    (version,digest,config,severity,threshold,hysteresis,hold_s,clear_s,quiet_s,
     business_start,business_end,schedule_timezone)=row
    if not isinstance(version,int) or version < 0:
        raise ValueError("Rule has no trustworthy version provenance")
    identity={"rule_id":rule_code,"device_id":device_id}
    for entity,key,value in (("RULE","external_rule_id",rule_code),
                             ("DEVICE","external_device_id",device_id)):
        cur.execute("""SELECT external_id,config_version FROM integration_identity_mappings
            WHERE system_code='nextgrp' AND entity_type=%s AND internal_id=%s
              AND confirmed=TRUE AND enabled=TRUE""", (entity,value))
        mapped=cur.fetchone()
        if mapped:
            identity[key],identity[f"{entity.lower()}_mapping_version"]=mapped
    cur.execute("SELECT org_code FROM devices WHERE device_id=%s",(device_id,))
    device_org=cur.fetchone()
    if device_org:
        identity["org_code"]=device_org[0]
        cur.execute("""SELECT external_id,config_version FROM integration_identity_mappings
            WHERE system_code='nextgrp' AND entity_type='ORGANIZATION' AND internal_id=%s
              AND confirmed=TRUE AND enabled=TRUE""",(device_org[0],))
        mapped_org=cur.fetchone()
        if mapped_org:
            identity["external_org_code"],identity["organization_mapping_version"]=mapped_org
    frozen_config={**(config or {}),"severity":severity,"threshold":threshold,
        "hysteresis":hysteresis,"hold_seconds":hold_s,"clear_hold_seconds":clear_s,
        "quiet_seconds":quiet_s,"business_start":business_start.isoformat() if business_start else None,
        "business_end":business_end.isoformat() if business_end else None,
        "schedule_timezone":schedule_timezone}
    # Bootstrap version 0 predates payload_hash. It is still a concrete local
    # Rule definition, so freeze a deterministic digest of exactly what was
    # observed; never infer a different version.
    if not digest:
        digest=hashlib.sha256(json.dumps(frozen_config,sort_keys=True,
            separators=(",",":"),default=str).encode()).hexdigest()
    return {"version":version,"payload_hash":digest,"config":frozen_config,
            "identity":identity,"provenance":"FROZEN"}


def _save_frozen_snapshot(cur, rule_code, device_id, snapshot):
    cur.execute("""UPDATE rule_runtime_state SET frozen_rule_version=%s,
        frozen_rule_payload_hash=%s,frozen_rule_config_json=%s,frozen_identity_json=%s,
        episode_provenance='FROZEN' WHERE rule_code=%s AND device_id=%s""",
        (snapshot["version"],snapshot["payload_hash"],Jsonb(snapshot["config"]),
         Jsonb(snapshot["identity"]),rule_code,device_id))


def _runtime_snapshot(version,digest,config,identity,provenance):
    if provenance == "LEGACY":
        return None
    if provenance != "FROZEN" or version is None or config is None:
        return None
    return {"version":version,"payload_hash":digest,"config":config,
            "identity":identity or {},"provenance":provenance}


def _manually_closed_uncleared_episode(cur, rule_code, device_id):
    """Find a Dev 1-closed episode whose physical condition has not cleared."""
    cur.execute("""SELECT m.internal_episode_key,m.episode_started_at
        FROM alarm_episode_mappings m
        JOIN alarm_lifecycle l ON l.wire_alarm_key=m.wire_alarm_key
        WHERE m.rule_code=%s AND m.device_id=%s AND l.state='closed'
          AND NOT EXISTS (
              SELECT 1 FROM alarm_occurrences a
              WHERE a.episode_key=m.internal_episode_key
                AND a.occurrence_kind='CLEARED')
        ORDER BY l.updated_at DESC LIMIT 1""", (rule_code, device_id))
    return cur.fetchone()


SCOPE_MATCH = """(
    (r.scope_type='DEVICE' AND r.device_id=v.device_id) OR
    (r.scope_type='PROFILE' AND r.scope_profile_code=v.profile_code) OR
    (r.scope_type='ZONE' AND r.scope_org_code=v.org_code AND r.scope_site_code=v.site_code
        AND r.scope_zone_code=v.zone_code) OR
    (r.scope_type='SITE' AND r.scope_org_code=v.org_code AND r.scope_site_code=v.site_code) OR
    (r.scope_type='ORGANIZATION' AND r.scope_org_code=v.org_code)
)"""


def threshold_transition(*, value, threshold, hysteresis, hold_seconds,
                         clear_hold_seconds, observed_at, entered_at,
                         recovery_started_at, active):
    """Return (hold_start, recovery_start, active, transition)."""
    if active:
        if value < threshold - hysteresis:
            recovery = recovery_started_at or observed_at
            if (observed_at - recovery).total_seconds() >= clear_hold_seconds:
                return None, None, False, "CLEARED"
            return None, recovery, True, None
        return None, None, True, None
    if value <= threshold:
        return None, None, False, None
    started = entered_at or observed_at
    if (observed_at - started).total_seconds() >= hold_seconds:
        return None, None, True, "TRIGGERED"
    return started, None, False, None


def is_business_hours(at_utc, start, end, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return False
    local = at_utc.replace(tzinfo=timezone.utc).astimezone(zone)
    if local.weekday() >= 5:
        return False
    local_time = local.time().replace(tzinfo=None)
    if start <= end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def door_should_fire(*, opened_at, now, hold_seconds, business_start,
                     business_end, schedule_timezone):
    # A schedule is optional.  Without one, open_limit_s is an unconditional
    # continuous-open hold; do not silently disable the Rule.
    if not all((business_start, business_end, schedule_timezone)):
        return (now - opened_at).total_seconds() >= hold_seconds
    if not is_business_hours(now, business_start, business_end, schedule_timezone):
        return True
    return (now - opened_at).total_seconds() >= hold_seconds


def _write_occurrence(cur, *, rule_code, device_id, severity, occurrence_kind,
                      observed_at, episode_key, event_type, details, episode_started_at=None,
                      rule_snapshot=None):
    alarm_key = _alarm_key(rule_code, device_id, episode_key, occurrence_kind)
    if occurrence_kind == "TRIGGERED":
        started = episode_started_at or observed_at
        wire_alarm_key = f"{rule_code}:{device_id}:{int(started.replace(tzinfo=timezone.utc).timestamp())}"
        if not rule_snapshot or rule_snapshot.get("provenance") != "FROZEN":
            raise ValueError("refusing occurrence without frozen Rule provenance")
        cur.execute("""INSERT INTO alarm_episode_mappings
            (internal_episode_key,wire_alarm_key,episode_started_at,rule_code,device_id,
             frozen_rule_version,frozen_rule_payload_hash,frozen_identity_json,episode_provenance)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'FROZEN')
            ON CONFLICT(internal_episode_key) DO NOTHING""",
            (episode_key,wire_alarm_key,started,rule_code,device_id,
             rule_snapshot["version"],rule_snapshot["payload_hash"],
             Jsonb(rule_snapshot.get("identity") or {})))
    cur.execute("""SELECT wire_alarm_key,episode_started_at,frozen_rule_version,
            frozen_rule_payload_hash,frozen_identity_json,episode_provenance
        FROM alarm_episode_mappings
        WHERE internal_episode_key=%s FOR UPDATE""", (episode_key,))
    mapping=cur.fetchone()
    if mapping is None:
        raise ValueError("alarm episode mapping is missing; refusing out-of-order occurrence")
    wire_alarm_key,episode_started,rule_version,rule_digest,frozen_identity,provenance=mapping
    if provenance != "FROZEN" or rule_version is None:
        raise ValueError("legacy episode has no trustworthy Rule provenance")
    cur.execute("SELECT count(*) FROM alarm_occurrences WHERE episode_key=%s", (episode_key,))
    occurrence_seq=int(cur.fetchone()[0]) + 1
    delivery_event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nextwireless:{alarm_key}"))
    mapped_event = ({("S1", "TRIGGERED"): "TEMPERATURE_HIGH",
                     ("S1", "CLEARED"): "TEMPERATURE_RECOVERED",
                     ("S6", "TRIGGERED"): "DOOR_OPENED",
                     ("S6", "CLEARED"): "DOOR_CLOSED"}
                    .get((rule_code.split("_")[0], occurrence_kind), event_type))
    summary = ({("S1", "TRIGGERED"): f"Temperature high on {device_id}",
                ("S1", "CLEARED"): f"Temperature recovered on {device_id}",
                ("S6", "TRIGGERED"): f"Door opened on {device_id}",
                ("S6", "CLEARED"): f"Door closed on {device_id}"}
               .get((rule_code.split("_")[0], occurrence_kind),
                    f"{mapped_event} on {device_id}"))
    details = {**details, "summary": summary}
    cur.execute("SELECT org_code,zone_code FROM devices WHERE device_id=%s",(device_id,))
    registry=cur.fetchone()
    if registry is None:
        raise ValueError("alarm device/rule registry entry is missing")
    org_code,zone_code=registry
    if not isinstance(rule_version, int) or rule_version < 0:
        raise ValueError("rule config_version is missing or invalid")
    severity_wire={"INFO":"Low","WARNING":"Medium","HIGH":"High","CRITICAL":"Critical"}.get(severity,severity)
    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"nextwireless:source:{alarm_key}"))
    payload={"event_id":delivery_event_id,"org_code":org_code,"alarm_key":wire_alarm_key,
        "episode_key":wire_alarm_key,
        "kind":"occurrence" if occurrence_kind=="TRIGGERED" else "cleared",
        "rule_id":rule_code,
        "rule_version":rule_version,"device_id":device_id,"zone_code":zone_code,
        "severity":severity_wire,"episode_start":episode_started.isoformat(timespec="milliseconds")+"Z",
        "occurrence_seq":occurrence_seq,"event":{"event_id":event_id,"event_type":mapped_event,
        "ts":observed_at.isoformat(timespec="milliseconds")+"Z","data":details}}
    payload["raised_at" if occurrence_kind=="TRIGGERED" else "cleared_at"]=observed_at.isoformat(timespec="milliseconds")+"Z"
    cur.execute("""INSERT INTO alarm_occurrences
        (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,event_type,
         triggered_at,cleared_at,payload_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (alarm_key) DO NOTHING""",
        (alarm_key, episode_key, occurrence_kind, rule_code, device_id, severity,
         event_type, observed_at, observed_at if occurrence_kind == "CLEARED" else None,
         Jsonb(payload)))
    if not cur.rowcount:
        return False
    cur.execute("""INSERT INTO alarm_outbox
        (alarm_key,payload_json,next_attempt_at,created_at,delivery_event_id,wire_alarm_key)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (alarm_key) DO NOTHING""",
        (alarm_key, Jsonb(payload), observed_at, observed_at, delivery_event_id, wire_alarm_key))
    return True


def on_telemetry(cur, *, device_id, metric, value, measured_at, received_at):
    cur.execute(f"""SELECT r.rule_code,r.threshold_value,r.hysteresis_value,r.hold_seconds,
            r.clear_hold_seconds,r.quiet_seconds,r.severity,v.telemetry_interval_seconds,r.enabled
            FROM rule_definitions r CROSS JOIN devices v
            LEFT JOIN rule_runtime_state s0 ON s0.rule_code=r.rule_code AND s0.device_id=v.device_id
            WHERE v.device_id=%s AND r.rule_kind='THRESHOLD' AND r.metric=%s
            AND (r.enabled=TRUE OR s0.episode_key IS NOT NULL OR s0.hold_started_at IS NOT NULL
                 OR s0.is_active=TRUE) AND {SCOPE_MATCH}
            ORDER BY r.rule_code""", (device_id, metric))
    alarms = 0
    for (rule_code, threshold, hysteresis, hold_s, clear_s, quiet_s, severity,
         interval_s, enabled) in cur.fetchall():
        if threshold is None or hysteresis < 0:
            continue
        # Persist delayed data, but never let a future/stale device timestamp
        # manufacture a current alarm. A normal QoS/reconnect delay remains allowed.
        skew_seconds = (received_at - measured_at).total_seconds()
        if skew_seconds < -300 or skew_seconds > max(300, int(interval_s) * 2):
            continue
        cur.execute("""INSERT INTO rule_runtime_state(rule_code,device_id)
            VALUES (%s,%s) ON CONFLICT (rule_code,device_id) DO NOTHING""", (rule_code, device_id))
        cur.execute("""SELECT hold_started_at,recovery_started_at,is_active,last_sample_at,
                last_fired_at,episode_key,episode_started_at,frozen_rule_version,
                frozen_rule_payload_hash,frozen_rule_config_json,frozen_identity_json,
                episode_provenance FROM rule_runtime_state
                WHERE rule_code=%s AND device_id=%s FOR UPDATE""", (rule_code, device_id))
        (hold_start,recovery,active,last_sample,last_fired,episode,episode_started,
         frozen_version,frozen_digest,frozen_config,frozen_identity,provenance)=cur.fetchone()
        if provenance == "LEGACY":
            continue
        snapshot=_runtime_snapshot(frozen_version,frozen_digest,frozen_config,
                                   frozen_identity,provenance)
        if snapshot:
            cfg=snapshot["config"]
            threshold=cfg.get("threshold",threshold)
            hysteresis=cfg.get("hysteresis",hysteresis)
            hold_s=cfg.get("hold_seconds",hold_s)
            clear_s=cfg.get("clear_hold_seconds",clear_s)
            quiet_s=cfg.get("quiet_seconds",quiet_s)
            severity=cfg.get("severity",severity)
        elif not enabled:
            continue
        if last_sample is not None and measured_at <= last_sample:
            continue
        # A gap longer than two configured samples breaks a continuous hold.
        if last_sample is not None and (measured_at - last_sample).total_seconds() > int(interval_s) * 2:
            if not active and hold_start is not None:
                hold_start = measured_at
            if active and recovery is not None:
                recovery = measured_at
        if not snapshot and not active and value > threshold:
            snapshot=_freeze_rule_snapshot(cur,rule_code,device_id)
            _save_frozen_snapshot(cur,rule_code,device_id,snapshot)
            episode=f"ep:{rule_code}:{_key(device_id,metric,measured_at)}"
            episode_started=measured_at
        next_hold, next_recovery, next_active, transition = threshold_transition(
            value=value, threshold=float(threshold), hysteresis=float(hysteresis),
            hold_seconds=int(hold_s), clear_hold_seconds=int(clear_s), observed_at=measured_at,
            entered_at=hold_start, recovery_started_at=recovery, active=bool(active))
        if transition == "TRIGGERED":
            allowed = last_fired is None or (measured_at - last_fired).total_seconds() >= quiet_s
            manually_closed = _manually_closed_uncleared_episode(cur, rule_code, device_id)
            if manually_closed:
                episode, episode_started = manually_closed
                next_active = True
                next_hold = None
            elif allowed:
                episode = episode or f"ep:{rule_code}:{_key(device_id, metric, hold_start or measured_at)}"
                episode_started = episode_started or hold_start or measured_at
                alarms += int(_write_occurrence(
                    cur, rule_code=rule_code, device_id=device_id, severity=severity,
                    occurrence_kind="TRIGGERED", observed_at=received_at, episode_key=episode,
                    episode_started_at=episode_started,
                    event_type="THRESHOLD_EXCEEDED",
                    details={"metric": metric, "value": value, "threshold": threshold,
                             "hold_seconds": hold_s, "hysteresis": hysteresis},
                    rule_snapshot=snapshot))
                last_fired = measured_at
            else:
                next_active = False
                # Preserve the already-satisfied hold while the quiet window runs.
                next_hold = hold_start or measured_at
        elif transition == "CLEARED" and episode:
            alarms += int(_write_occurrence(
                cur, rule_code=rule_code, device_id=device_id, severity=severity,
                occurrence_kind="CLEARED", observed_at=received_at, episode_key=episode,
                event_type="THRESHOLD_CLEARED",
                details={"metric": metric, "value": value,
                         "recovery_threshold": float(threshold) - float(hysteresis),
                         "clear_hold_seconds": clear_s}))
            episode = episode_started = None
            snapshot = None
        if not next_active and next_hold is None and episode is not None and value <= threshold:
            episode=episode_started=None
            snapshot=None
        clear_snapshot = snapshot is None and episode is None
        cur.execute("""UPDATE rule_runtime_state SET hold_started_at=%s,recovery_started_at=%s,
                is_active=%s,last_sample_at=%s,last_fired_at=%s,episode_key=%s,
                episode_started_at=%s,
                frozen_rule_version=CASE WHEN %s THEN NULL ELSE frozen_rule_version END,
                frozen_rule_payload_hash=CASE WHEN %s THEN NULL ELSE frozen_rule_payload_hash END,
                frozen_rule_config_json=CASE WHEN %s THEN NULL ELSE frozen_rule_config_json END,
                frozen_identity_json=CASE WHEN %s THEN NULL ELSE frozen_identity_json END,
                episode_provenance=CASE WHEN %s THEN NULL ELSE episode_provenance END
                WHERE rule_code=%s AND device_id=%s""",
                (next_hold, next_recovery, next_active, measured_at, last_fired, episode,
                 episode_started,clear_snapshot,clear_snapshot,clear_snapshot,clear_snapshot,
                 clear_snapshot,rule_code,device_id))
    return alarms


def on_event(cur, *, device_id, event_id, event_type, received_at, payload,
             observed_at=None):
    observed_at = observed_at or received_at
    cur.execute(f"""SELECT r.rule_code,r.severity,r.hold_seconds,r.clear_hold_seconds,r.quiet_seconds,
            r.business_start,r.business_end,r.schedule_timezone,r.enabled FROM rule_definitions r
            CROSS JOIN devices v
            LEFT JOIN rule_runtime_state s0 ON s0.rule_code=r.rule_code AND s0.device_id=v.device_id
            WHERE v.device_id=%s AND r.rule_kind='STATE_CHANGE'
              AND (r.enabled=TRUE OR s0.episode_key IS NOT NULL OR s0.current_state='open'
                   OR s0.is_active=TRUE) AND r.event_type='DOOR_OPENED' AND {SCOPE_MATCH}
            ORDER BY r.rule_code""", (device_id,))
    changes = 0
    for rule_code,severity,hold_s,clear_s,quiet_s,start,end,tz_name,enabled in cur.fetchall():
        cur.execute("""INSERT INTO rule_runtime_state(rule_code,device_id)
            VALUES (%s,%s) ON CONFLICT (rule_code,device_id) DO NOTHING""", (rule_code, device_id))
        cur.execute("""SELECT current_state,state_changed_at,is_active,last_fired_at,episode_key,
            source_event_id,frozen_rule_version,frozen_rule_payload_hash,frozen_rule_config_json,
            frozen_identity_json,episode_provenance FROM rule_runtime_state
            WHERE rule_code=%s AND device_id=%s FOR UPDATE""",
            (rule_code, device_id))
        (state,changed_at,active,last_fired,episode,source_event,frozen_version,
         frozen_digest,frozen_config,frozen_identity,provenance)=cur.fetchone()
        snapshot=_runtime_snapshot(frozen_version,frozen_digest,frozen_config,
                                   frozen_identity,provenance)
        if snapshot:
            cfg=snapshot["config"]
            severity=cfg.get("severity",severity); hold_s=cfg.get("hold_seconds",hold_s)
            clear_s=cfg.get("clear_hold_seconds",clear_s); quiet_s=cfg.get("quiet_seconds",quiet_s)
        if changed_at is not None and observed_at <= changed_at:
            continue
        if event_type == "DOOR_OPENED":
            if state == "open" or not enabled:
                continue
            snapshot=_freeze_rule_snapshot(cur,rule_code,device_id)
            _save_frozen_snapshot(cur,rule_code,device_id,snapshot)
            episode = f"ep:{rule_code}:{_key(device_id, event_id)}"
            cur.execute("""UPDATE rule_runtime_state SET current_state='open',state_changed_at=%s,
                source_event_id=%s,episode_key=%s,episode_started_at=%s,recovery_started_at=NULL
                WHERE rule_code=%s AND device_id=%s""",
                # Keep the device observation for audit/order checks, but use
                # server receive time as the Rule timer origin. Mixing a
                # device clock with the server clock can otherwise shorten or
                # extend the configured open limit when SNTP is skewed.
                (observed_at, event_id, episode, received_at, rule_code, device_id))
        elif event_type == "DOOR_CLOSED":
            if state != "open":
                continue
            if provenance == "LEGACY":
                active=False
            if active and episode and clear_s == 0 and snapshot:
                changes += int(_write_occurrence(
                    cur, rule_code=rule_code, device_id=device_id, severity=severity,
                    occurrence_kind="CLEARED", observed_at=received_at, episode_key=episode,
                    event_type="DOOR_CLOSED", details={"device_event_id": event_id,
                    "clear_hold_seconds": clear_s}))
            keep_episode = bool(active and episode and clear_s > 0)
            clear_snapshot=not keep_episode
            cur.execute("""UPDATE rule_runtime_state SET current_state='closed',state_changed_at=%s,
                source_event_id=%s,is_active=%s,episode_key=%s,
                episode_started_at=CASE WHEN %s THEN episode_started_at ELSE NULL END,
                recovery_started_at=%s,
                frozen_rule_version=CASE WHEN %s THEN NULL ELSE frozen_rule_version END,
                frozen_rule_payload_hash=CASE WHEN %s THEN NULL ELSE frozen_rule_payload_hash END,
                frozen_rule_config_json=CASE WHEN %s THEN NULL ELSE frozen_rule_config_json END,
                frozen_identity_json=CASE WHEN %s THEN NULL ELSE frozen_identity_json END,
                episode_provenance=CASE WHEN %s THEN NULL ELSE episode_provenance END
                WHERE rule_code=%s AND device_id=%s""",
                (observed_at, event_id, keep_episode, episode if keep_episode else None,
                 keep_episode, received_at if keep_episode else None,clear_snapshot,clear_snapshot,
                 clear_snapshot,clear_snapshot,clear_snapshot,rule_code,device_id))
    return changes + evaluate_due_rules(cur, received_at, device_id=device_id)


def evaluate_due_rules(cur, now, device_id=None):
    # Complete configured delayed door clears first.
    cur.execute("""SELECT d.rule_code,s.device_id,d.severity,d.clear_hold_seconds,
            s.recovery_started_at,s.episode_key,s.source_event_id,s.frozen_rule_version,
            s.frozen_rule_payload_hash,s.frozen_rule_config_json,s.frozen_identity_json,
            s.episode_provenance
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code
        WHERE d.rule_kind='STATE_CHANGE' AND s.current_state='closed'
          AND s.is_active=TRUE AND s.recovery_started_at IS NOT NULL
          AND (%s::text IS NULL OR s.device_id=%s) FOR UPDATE OF s""", (device_id, device_id))
    fired = 0
    for (code,dev,severity,clear_s,recovery,episode,source_event,frozen_version,
         frozen_digest,frozen_config,frozen_identity,provenance) in cur.fetchall():
        snapshot=_runtime_snapshot(frozen_version,frozen_digest,frozen_config,frozen_identity,provenance)
        if not snapshot:
            continue
        clear_s=snapshot["config"].get("clear_hold_seconds",clear_s)
        severity=snapshot["config"].get("severity",severity)
        if (now - recovery).total_seconds() < clear_s:
            continue
        fired += int(_write_occurrence(
            cur, rule_code=code, device_id=dev, severity=severity,
            occurrence_kind="CLEARED", observed_at=now, episode_key=episode,
            event_type="DOOR_CLOSED", details={"device_event_id": source_event,
            "clear_hold_seconds": clear_s}))
        cur.execute("""UPDATE rule_runtime_state SET is_active=FALSE,episode_key=NULL,
            episode_started_at=NULL,recovery_started_at=NULL,frozen_rule_version=NULL,
            frozen_rule_payload_hash=NULL,frozen_rule_config_json=NULL,frozen_identity_json=NULL,
            episode_provenance=NULL
            WHERE rule_code=%s AND device_id=%s""", (code, dev))
    cur.execute("""SELECT d.rule_code,s.device_id,d.severity,d.hold_seconds,d.quiet_seconds,
            d.business_start,d.business_end,d.schedule_timezone,s.state_changed_at,
            s.episode_started_at,s.last_fired_at,s.episode_key,s.source_event_id,
            s.frozen_rule_version,s.frozen_rule_payload_hash,s.frozen_rule_config_json,
            s.frozen_identity_json,s.episode_provenance
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code
        WHERE d.rule_kind='STATE_CHANGE' AND s.current_state='open'
          AND s.is_active=FALSE AND (%s::text IS NULL OR s.device_id=%s)
        FOR UPDATE OF s""", (device_id, device_id))
    for (code,dev,severity,hold_s,quiet_s,start,end,tz_name,device_opened_at,
         timer_started_at,last_fired,episode,source_event,frozen_version,frozen_digest,
         frozen_config,frozen_identity,provenance) in cur.fetchall():
        snapshot=_runtime_snapshot(frozen_version,frozen_digest,frozen_config,frozen_identity,provenance)
        if not snapshot:
            continue
        cfg=snapshot["config"]
        severity=cfg.get("severity",severity); hold_s=cfg.get("hold_seconds",hold_s)
        quiet_s=cfg.get("quiet_seconds",quiet_s)
        start=cfg.get("business_start"); end=cfg.get("business_end")
        tz_name=cfg.get("schedule_timezone")
        if isinstance(start,str):
            from datetime import time as wall_time
            start=wall_time.fromisoformat(start)
        if isinstance(end,str):
            from datetime import time as wall_time
            end=wall_time.fromisoformat(end)
        if not all((timer_started_at, episode)):
            continue
        if not door_should_fire(opened_at=timer_started_at, now=now, hold_seconds=hold_s,
                                business_start=start, business_end=end, schedule_timezone=tz_name):
            continue
        if last_fired and (now - last_fired).total_seconds() < quiet_s:
            continue
        has_schedule = all((start, end, tz_name))
        outside = has_schedule and not is_business_hours(now, start, end, tz_name)
        created = _write_occurrence(
            cur, rule_code=code, device_id=dev, severity=severity,
            occurrence_kind="TRIGGERED", observed_at=now, episode_key=episode,
            episode_started_at=timer_started_at,
            event_type="DOOR_OPEN_OUTSIDE_HOURS" if outside else "DOOR_OPEN_TOO_LONG",
            details={"device_event_id": source_event,
                     "opened_at": device_opened_at.isoformat() + "Z",
                     "hold_seconds": hold_s, "schedule_timezone": tz_name},
            rule_snapshot=snapshot)
        cur.execute("""UPDATE rule_runtime_state SET is_active=TRUE,last_fired_at=%s
            WHERE rule_code=%s AND device_id=%s""", (now, code, dev))
        fired += int(created)
    return fired

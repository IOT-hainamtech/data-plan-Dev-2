"""Durable M1 S1/S6 rule state machines using the caller's DB transaction."""
import hashlib
import uuid
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def _alarm_key(rule_code, device_id, source_key, occurrence_kind="TRIGGERED"):
    return f"m1:{rule_code}:{_key(rule_code, device_id, source_key, occurrence_kind)}"


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
    if not is_business_hours(now, business_start, business_end, schedule_timezone):
        return True
    return (now - opened_at).total_seconds() >= hold_seconds


def _write_occurrence(cur, *, rule_code, device_id, severity, occurrence_kind,
                      observed_at, episode_key, event_type, details, episode_started_at=None):
    alarm_key = _alarm_key(rule_code, device_id, episode_key, occurrence_kind)
    if occurrence_kind == "TRIGGERED":
        started = episode_started_at or observed_at
        wire_alarm_key = f"{rule_code}:{device_id}:{int(started.replace(tzinfo=timezone.utc).timestamp())}"
        cur.execute("""INSERT INTO alarm_episode_mappings
            (internal_episode_key,wire_alarm_key,episode_started_at,rule_code,device_id)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(internal_episode_key) DO NOTHING""",
            (episode_key,wire_alarm_key,started,rule_code,device_id))
    cur.execute("SELECT wire_alarm_key,episode_started_at FROM alarm_episode_mappings WHERE internal_episode_key=%s",(episode_key,))
    mapping=cur.fetchone()
    wire_alarm_key=mapping[0] if mapping else alarm_key
    episode_started=mapping[1] if mapping else (episode_started_at or observed_at)
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
    cur.execute("""SELECT d.org_code,d.zone_code,r.config_version
        FROM devices d JOIN rule_definitions r ON r.rule_code=%s WHERE d.device_id=%s""",
        (rule_code,device_id))
    org_code,zone_code,rule_version=cur.fetchone()
    severity_wire={"INFO":"Low","WARNING":"Medium","HIGH":"High","CRITICAL":"Critical"}.get(severity,severity)
    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"nextwireless:source:{alarm_key}"))
    payload={"event_id":delivery_event_id,"org_code":org_code,"alarm_key":wire_alarm_key,
        "episode_key":wire_alarm_key,
        "kind":"occurrence" if occurrence_kind=="TRIGGERED" else "cleared",
        "rule_id":rule_code,
        "rule_version":max(1,rule_version),"device_id":device_id,"zone_code":zone_code,
        "severity":severity_wire,"episode_start":episode_started.isoformat(timespec="milliseconds")+"Z",
        "occurrence_seq":1,"event":{"event_id":event_id,"event_type":mapped_event,
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
            r.clear_hold_seconds,r.quiet_seconds,r.severity FROM rule_definitions r
            CROSS JOIN devices v WHERE v.device_id=%s AND r.rule_kind='THRESHOLD'
            AND r.metric=%s AND r.enabled=TRUE AND {SCOPE_MATCH}
            ORDER BY r.rule_code""", (device_id, metric))
    alarms = 0
    for rule_code, threshold, hysteresis, hold_s, clear_s, quiet_s, severity in cur.fetchall():
        if threshold is None or hysteresis < 0:
            continue
        cur.execute("""INSERT INTO rule_runtime_state(rule_code,device_id)
            VALUES (%s,%s) ON CONFLICT (rule_code,device_id) DO NOTHING""", (rule_code, device_id))
        cur.execute("""SELECT hold_started_at,recovery_started_at,is_active,last_sample_at,
                last_fired_at,episode_key,episode_started_at FROM rule_runtime_state
                WHERE rule_code=%s AND device_id=%s FOR UPDATE""", (rule_code, device_id))
        hold_start, recovery, active, last_sample, last_fired, episode, episode_started = cur.fetchone()
        if last_sample is not None and measured_at <= last_sample:
            continue
        # URD continuity requirement: if sample gap exceeds 2x interval (120s), reset continuity window
        if last_sample is not None and (measured_at - last_sample).total_seconds() > 120:
            if not active and hold_start is not None:
                hold_start = measured_at
            if active and recovery is not None:
                recovery = measured_at
        next_hold, next_recovery, next_active, transition = threshold_transition(
            value=value, threshold=float(threshold), hysteresis=float(hysteresis),
            hold_seconds=int(hold_s), clear_hold_seconds=int(clear_s), observed_at=measured_at,
            entered_at=hold_start, recovery_started_at=recovery, active=bool(active))
        if transition == "TRIGGERED":
            allowed = last_fired is None or (measured_at - last_fired).total_seconds() >= quiet_s
            if allowed:
                episode = f"ep:{rule_code}:{_key(device_id, metric, hold_start or measured_at)}"
                episode_started = hold_start or measured_at
                alarms += int(_write_occurrence(
                    cur, rule_code=rule_code, device_id=device_id, severity=severity,
                    occurrence_kind="TRIGGERED", observed_at=received_at, episode_key=episode,
                    episode_started_at=episode_started,
                    event_type="THRESHOLD_EXCEEDED",
                    details={"metric": metric, "value": value, "threshold": threshold,
                             "hold_seconds": hold_s, "hysteresis": hysteresis}))
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
        cur.execute("""UPDATE rule_runtime_state SET hold_started_at=%s,recovery_started_at=%s,
                is_active=%s,last_sample_at=%s,last_fired_at=%s,episode_key=%s,
                episode_started_at=%s WHERE rule_code=%s AND device_id=%s""",
                (next_hold, next_recovery, next_active, measured_at, last_fired, episode,
                 episode_started, rule_code, device_id))
    return alarms


def on_event(cur, *, device_id, event_id, event_type, received_at, payload,
             observed_at=None):
    observed_at = observed_at or received_at
    cur.execute(f"""SELECT r.rule_code,r.severity,r.hold_seconds,r.clear_hold_seconds,r.quiet_seconds,
            r.business_start,r.business_end,r.schedule_timezone FROM rule_definitions r
            CROSS JOIN devices v WHERE v.device_id=%s AND r.rule_kind='STATE_CHANGE'
              AND r.enabled=TRUE AND r.event_type='DOOR_OPENED' AND {SCOPE_MATCH}
            ORDER BY r.rule_code""", (device_id,))
    changes = 0
    for rule_code, severity, hold_s, clear_s, quiet_s, start, end, tz_name in cur.fetchall():
        if start is None or end is None or not tz_name:
            continue
        cur.execute("""INSERT INTO rule_runtime_state(rule_code,device_id)
            VALUES (%s,%s) ON CONFLICT (rule_code,device_id) DO NOTHING""", (rule_code, device_id))
        cur.execute("""SELECT current_state,state_changed_at,is_active,last_fired_at,episode_key,
            source_event_id FROM rule_runtime_state WHERE rule_code=%s AND device_id=%s FOR UPDATE""",
            (rule_code, device_id))
        state, changed_at, active, last_fired, episode, source_event = cur.fetchone()
        if changed_at is not None and observed_at <= changed_at:
            continue
        if event_type == "DOOR_OPENED":
            if state == "open":
                continue
            episode = f"ep:{rule_code}:{_key(device_id, event_id)}"
            cur.execute("""UPDATE rule_runtime_state SET current_state='open',state_changed_at=%s,
                source_event_id=%s,episode_key=%s,episode_started_at=%s,recovery_started_at=NULL
                WHERE rule_code=%s AND device_id=%s""",
                (observed_at, event_id, episode, observed_at, rule_code, device_id))
        elif event_type == "DOOR_CLOSED":
            if state != "open":
                continue
            if active and episode and clear_s == 0:
                changes += int(_write_occurrence(
                    cur, rule_code=rule_code, device_id=device_id, severity=severity,
                    occurrence_kind="CLEARED", observed_at=received_at, episode_key=episode,
                    event_type="DOOR_CLOSED", details={"device_event_id": event_id,
                    "clear_hold_seconds": clear_s}))
            keep_episode = bool(active and episode and clear_s > 0)
            cur.execute("""UPDATE rule_runtime_state SET current_state='closed',state_changed_at=%s,
                source_event_id=%s,is_active=%s,episode_key=%s,
                episode_started_at=CASE WHEN %s THEN episode_started_at ELSE NULL END,
                recovery_started_at=%s WHERE rule_code=%s AND device_id=%s""",
                (observed_at, event_id, keep_episode, episode if keep_episode else None,
                 keep_episode, observed_at if keep_episode else None, rule_code, device_id))
    return changes + evaluate_due_rules(cur, received_at, device_id=device_id)


def evaluate_due_rules(cur, now, device_id=None):
    # Complete configured delayed door clears first.
    cur.execute("""SELECT d.rule_code,s.device_id,d.severity,d.clear_hold_seconds,
            s.recovery_started_at,s.episode_key,s.source_event_id
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code
        WHERE d.enabled=TRUE AND d.rule_kind='STATE_CHANGE' AND s.current_state='closed'
          AND s.is_active=TRUE AND s.recovery_started_at IS NOT NULL
          AND (%s::text IS NULL OR s.device_id=%s) FOR UPDATE OF s""", (device_id, device_id))
    fired = 0
    for code, dev, severity, clear_s, recovery, episode, source_event in cur.fetchall():
        if (now - recovery).total_seconds() < clear_s:
            continue
        fired += int(_write_occurrence(
            cur, rule_code=code, device_id=dev, severity=severity,
            occurrence_kind="CLEARED", observed_at=now, episode_key=episode,
            event_type="DOOR_CLOSED", details={"device_event_id": source_event,
            "clear_hold_seconds": clear_s}))
        cur.execute("""UPDATE rule_runtime_state SET is_active=FALSE,episode_key=NULL,
            episode_started_at=NULL,recovery_started_at=NULL
            WHERE rule_code=%s AND device_id=%s""", (code, dev))
    cur.execute("""SELECT d.rule_code,s.device_id,d.severity,d.hold_seconds,d.quiet_seconds,
            d.business_start,d.business_end,d.schedule_timezone,s.state_changed_at,
            s.last_fired_at,s.episode_key,s.source_event_id
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code
        WHERE d.enabled=TRUE AND d.rule_kind='STATE_CHANGE' AND s.current_state='open'
          AND s.is_active=FALSE AND (%s::text IS NULL OR s.device_id=%s)
        FOR UPDATE OF s""", (device_id, device_id))
    for code, dev, severity, hold_s, quiet_s, start, end, tz_name, opened_at, last_fired, episode, source_event in cur.fetchall():
        if not all((start, end, tz_name, opened_at, episode)):
            continue
        if not door_should_fire(opened_at=opened_at, now=now, hold_seconds=hold_s,
                                business_start=start, business_end=end, schedule_timezone=tz_name):
            continue
        if last_fired and (now - last_fired).total_seconds() < quiet_s:
            continue
        outside = not is_business_hours(now, start, end, tz_name)
        created = _write_occurrence(
            cur, rule_code=code, device_id=dev, severity=severity,
            occurrence_kind="TRIGGERED", observed_at=now, episode_key=episode,
            episode_started_at=opened_at,
            event_type="DOOR_OPEN_OUTSIDE_HOURS" if outside else "DOOR_OPEN_TOO_LONG",
            details={"device_event_id": source_event, "opened_at": opened_at.isoformat() + "Z",
                     "hold_seconds": hold_s, "schedule_timezone": tz_name})
        cur.execute("""UPDATE rule_runtime_state SET is_active=TRUE,last_fired_at=%s
            WHERE rule_code=%s AND device_id=%s""", (now, code, dev))
        fired += int(created)
    return fired

"""Durable M1 S1/S6 rule state machines using the caller's DB transaction."""
import hashlib
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def _alarm_key(rule_code, device_id, source_key, occurrence_kind="TRIGGERED"):
    return f"m1:{rule_code}:{_key(rule_code, device_id, source_key, occurrence_kind)}"


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
                      observed_at, episode_key, event_type, details):
    alarm_key = _alarm_key(rule_code, device_id, episode_key, occurrence_kind)
    payload = {
        "alarm_key": alarm_key, "episode_key": episode_key, "schema_version": 1,
        "rule_code": rule_code, "device_id": device_id, "severity": severity,
        "occurrence_kind": occurrence_kind, "event_type": event_type,
        "ts": observed_at.isoformat(timespec="milliseconds") + "Z", "details": details,
    }
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
        (alarm_key,payload_json,next_attempt_at,created_at)
        VALUES (%s,%s,%s,%s) ON CONFLICT (alarm_key) DO NOTHING""",
        (alarm_key, Jsonb(payload), observed_at, observed_at))
    return True


def on_telemetry(cur, *, device_id, metric, value, measured_at, received_at):
    cur.execute("""SELECT rule_code,threshold_value,hysteresis_value,hold_seconds,
            clear_hold_seconds,quiet_seconds,severity FROM rule_definitions
            WHERE device_id=%s AND rule_kind='THRESHOLD' AND metric=%s AND enabled=TRUE
            ORDER BY rule_code""", (device_id, metric))
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
        next_hold, next_recovery, next_active, transition = threshold_transition(
            value=value, threshold=float(threshold), hysteresis=float(hysteresis),
            hold_seconds=int(hold_s), clear_hold_seconds=int(clear_s), observed_at=received_at,
            entered_at=hold_start, recovery_started_at=recovery, active=bool(active))
        if transition == "TRIGGERED":
            allowed = last_fired is None or (received_at - last_fired).total_seconds() >= quiet_s
            if allowed:
                episode = f"ep:{rule_code}:{_key(device_id, metric, hold_start or received_at)}"
                episode_started = hold_start or received_at
                alarms += int(_write_occurrence(
                    cur, rule_code=rule_code, device_id=device_id, severity=severity,
                    occurrence_kind="TRIGGERED", observed_at=received_at, episode_key=episode,
                    event_type="THRESHOLD_EXCEEDED",
                    details={"metric": metric, "value": value, "threshold": threshold,
                             "hold_seconds": hold_s, "hysteresis": hysteresis}))
                last_fired = received_at
            else:
                next_active = False
                # Preserve the already-satisfied hold while the quiet window runs.
                next_hold = hold_start or received_at
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
    cur.execute("""SELECT rule_code,severity,hold_seconds,clear_hold_seconds,quiet_seconds,
            business_start,business_end,schedule_timezone FROM rule_definitions
            WHERE device_id=%s AND rule_kind='STATE_CHANGE' AND enabled=TRUE
              AND event_type='DOOR_OPENED' ORDER BY rule_code""", (device_id,))
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
    cur.execute("""SELECT d.rule_code,d.device_id,d.severity,d.clear_hold_seconds,
            s.recovery_started_at,s.episode_key,s.source_event_id
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code AND s.device_id=d.device_id
        WHERE d.enabled=TRUE AND d.rule_kind='STATE_CHANGE' AND s.current_state='closed'
          AND s.is_active=TRUE AND s.recovery_started_at IS NOT NULL
          AND (%s::text IS NULL OR d.device_id=%s) FOR UPDATE OF s""", (device_id, device_id))
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
    cur.execute("""SELECT d.rule_code,d.device_id,d.severity,d.hold_seconds,d.quiet_seconds,
            d.business_start,d.business_end,d.schedule_timezone,s.state_changed_at,
            s.last_fired_at,s.episode_key,s.source_event_id
        FROM rule_definitions d JOIN rule_runtime_state s
          ON s.rule_code=d.rule_code AND s.device_id=d.device_id
        WHERE d.enabled=TRUE AND d.rule_kind='STATE_CHANGE' AND s.current_state='open'
          AND s.is_active=FALSE AND (%s::text IS NULL OR d.device_id=%s)
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
            event_type="DOOR_OPEN_OUTSIDE_HOURS" if outside else "DOOR_OPEN_TOO_LONG",
            details={"device_event_id": source_event, "opened_at": opened_at.isoformat() + "Z",
                     "hold_seconds": hold_s, "schedule_timezone": tz_name})
        cur.execute("""UPDATE rule_runtime_state SET is_active=TRUE,last_fired_at=%s
            WHERE rule_code=%s AND device_id=%s""", (now, code, dev))
        fired += int(created)
    return fired

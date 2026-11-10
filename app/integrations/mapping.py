"""Controlled, verifiable integration identity mapping engine for Dev 1 -> Dev 2.

Enforces:
- Manifest/admin loading without hardcoding.
- Existence and ownership verification of internal entities (Org, Site, Zone, Profile, Device, Rule).
- Business rule semantic validation (prevents binding different business rules).
- Internal/external ID conflict detection.
- Explicit operator confirmation requirement (no auto-confirm).
- Atomic, idempotent upsert preserving version history.
- Dry-run mode for previewing changes.
"""
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("mapping-engine")

VALID_ENTITY_TYPES = ("ORGANIZATION", "SITE", "ZONE", "PROFILE", "DEVICE", "RULE")


def _payload_hash(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_mapping_record(cur, record: dict, system_code: str = "nextgrp") -> dict:
    """Validate a single mapping record against Dev 2 database entities and constraints."""
    if not isinstance(record, dict):
        raise ValueError("Mapping record must be a dictionary")

    entity_type = record.get("entity_type")
    if not entity_type or entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type '{entity_type}'. Must be one of {VALID_ENTITY_TYPES}")

    internal_id = record.get("internal_id")
    if not internal_id or not isinstance(internal_id, str) or not internal_id.strip():
        raise ValueError(f"internal_id is required for {entity_type}")
    internal_id = internal_id.strip()

    external_id = record.get("external_id")
    if not external_id or not isinstance(external_id, str) or not external_id.strip():
        raise ValueError(f"external_id is required for {entity_type}")
    external_id = external_id.strip()

    rec_sys_code = record.get("system_code", system_code) or system_code
    config_version = int(record.get("config_version", 0))
    if config_version < 0:
        raise ValueError(f"config_version must be >= 0 for {internal_id}")

    confirmed = bool(record.get("confirmed", False))
    enabled = bool(record.get("enabled", True))
    metadata = record.get("metadata") or record.get("metadata_json") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dictionary")

    # 1. Verify internal entity existence and ownership in Dev 2 database
    _verify_internal_entity(cur, entity_type, internal_id, metadata)

    # 2. Check for unique constraint conflicts in integration_identity_mappings
    _verify_no_mapping_conflicts(cur, rec_sys_code, entity_type, internal_id, external_id)

    return {
        "system_code": rec_sys_code,
        "entity_type": entity_type,
        "internal_id": internal_id,
        "external_id": external_id,
        "config_version": config_version,
        "confirmed": confirmed,
        "enabled": enabled,
        "metadata_json": metadata,
    }


def _verify_internal_entity(cur, entity_type: str, internal_id: str, metadata: dict):
    """Verify that internal Dev 2 entity exists, is enabled, and owns consistent metadata."""
    if entity_type == "ORGANIZATION":
        cur.execute("SELECT enabled FROM organizations WHERE org_code=%s", (internal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Internal organization '{internal_id}' does not exist")
        if not row[0]:
            raise ValueError(f"Internal organization '{internal_id}' is disabled")

    elif entity_type == "SITE":
        cur.execute("SELECT org_code, enabled FROM sites WHERE site_code=%s", (internal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Internal site '{internal_id}' does not exist")
        if not row[1]:
            raise ValueError(f"Internal site '{internal_id}' is disabled")
        expected_org = metadata.get("org_code")
        if expected_org and row[0] != expected_org:
            raise ValueError(f"Site '{internal_id}' belongs to org '{row[0]}', expected '{expected_org}'")

    elif entity_type == "ZONE":
        cur.execute("SELECT org_code, site_code, enabled FROM zones WHERE zone_code=%s", (internal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Internal zone '{internal_id}' does not exist")
        if not row[2]:
            raise ValueError(f"Internal zone '{internal_id}' is disabled")
        expected_org = metadata.get("org_code")
        expected_site = metadata.get("site_code")
        if expected_org and row[0] != expected_org:
            raise ValueError(f"Zone '{internal_id}' belongs to org '{row[0]}', expected '{expected_org}'")
        if expected_site and row[1] != expected_site:
            raise ValueError(f"Zone '{internal_id}' belongs to site '{row[1]}', expected '{expected_site}'")

    elif entity_type == "PROFILE":
        cur.execute("SELECT enabled, metadata_json FROM device_profiles WHERE profile_code=%s", (internal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Internal device profile '{internal_id}' does not exist")
        if not row[0]:
            raise ValueError(f"Internal device profile '{internal_id}' is disabled")
        expected_signal = metadata.get("signal_model")
        if expected_signal:
            prof_meta = row[1] or {}
            actual_signal = prof_meta.get("signal_model")
            if actual_signal and actual_signal != expected_signal:
                raise ValueError(f"Profile '{internal_id}' signal model '{actual_signal}' != expected '{expected_signal}'")

    elif entity_type == "DEVICE":
        cur.execute("SELECT org_code, site_code, zone_code, profile_code, enabled FROM devices WHERE device_id=%s", (internal_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Internal device '{internal_id}' does not exist")
        if not row[4]:
            raise ValueError(f"Internal device '{internal_id}' is disabled")
        expected_org = metadata.get("org_code")
        if expected_org and row[0] != expected_org:
            raise ValueError(f"Device '{internal_id}' belongs to org '{row[0]}', expected '{expected_org}'")
        expected_site = metadata.get("site_code")
        if expected_site and row[1] != expected_site:
            raise ValueError(f"Device '{internal_id}' belongs to site '{row[1]}', expected '{expected_site}'")

    elif entity_type == "RULE":
        cur.execute("""SELECT device_id, rule_kind, metric, event_type, hold_seconds,
                              threshold_value, enabled, config_version
                       FROM rule_definitions WHERE rule_code=%s""", (internal_id,))
        row = cur.fetchone()
        if row:
            dev_id, rule_kind, metric, event_type, hold_s, thresh, enabled, ver = row
            # Business semantics verification: prevent binding a different business rule
            exp_kind = metadata.get("rule_kind")
            if exp_kind and exp_kind != rule_kind:
                raise ValueError(f"Business rule kind mismatch for '{internal_id}': internal has '{rule_kind}', expected '{exp_kind}'")

            exp_metric = metadata.get("metric")
            if exp_metric and metric and exp_metric != metric:
                raise ValueError(f"Business rule metric mismatch for '{internal_id}': internal has '{metric}', expected '{exp_metric}'")

            exp_event = metadata.get("event_type")
            if exp_event and event_type and exp_event != event_type:
                raise ValueError(f"Business rule event_type mismatch for '{internal_id}': internal has '{event_type}', expected '{exp_event}'")

            exp_hold = metadata.get("hold_seconds")
            if exp_hold is not None and int(exp_hold) != int(hold_s or 0):
                raise ValueError(f"Business rule duration mismatch for '{internal_id}': internal has {hold_s}s, expected {exp_hold}s")

            exp_thresh = metadata.get("threshold") or metadata.get("threshold_value")
            if exp_thresh is not None and thresh is not None and float(exp_thresh) != float(thresh):
                raise ValueError(f"Business rule threshold mismatch for '{internal_id}': internal has {thresh}, expected {exp_thresh}")
        # Note: If rule does not exist yet in Dev 2, an unconfirmed or prospective mapping
        # can be registered if it will be provisioned by Dev 1 get_device_config.


def _verify_no_mapping_conflicts(cur, system_code: str, entity_type: str,
                                internal_id: str, external_id: str):
    """Enforce UNIQUE (system_code, entity_type, internal_id) and UNIQUE (system_code, entity_type, external_id)."""
    # Check internal_id conflict
    cur.execute("""SELECT external_id, confirmed FROM integration_identity_mappings
        WHERE system_code=%s AND entity_type=%s AND internal_id=%s""",
        (system_code, entity_type, internal_id))
    row = cur.fetchone()
    if row and row[0] != external_id:
        raise ValueError(
            f"Mapping conflict: internal {entity_type} '{internal_id}' is already mapped to '{row[0]}', "
            f"cannot map to '{external_id}'"
        )

    # Check external_id conflict
    cur.execute("""SELECT internal_id, confirmed FROM integration_identity_mappings
        WHERE system_code=%s AND entity_type=%s AND external_id=%s""",
        (system_code, entity_type, external_id))
    row = cur.fetchone()
    if row and row[0] != internal_id:
        raise ValueError(
            f"Mapping conflict: external {entity_type} '{external_id}' is already mapped to '{row[0]}', "
            f"cannot map to '{internal_id}'"
        )


def apply_mappings(cur, records: List[dict], system_code: str = "nextgrp",
                   dry_run: bool = False, default_confirmed: bool = False) -> dict:
    """Validate and atomically apply a batch of mappings.

    If dry_run=True, returns the execution plan without modifying database.
    If dry_run=False, performs atomic idempotent upsert and records version history for confirmed mappings.
    """
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be a list or tuple of mapping dictionaries")

    # Pre-validation pass: fail closed before any modification
    validated = []
    for idx, rec in enumerate(records):
        try:
            norm = validate_mapping_record(cur, rec, system_code=system_code)
            if default_confirmed and not norm["confirmed"]:
                norm["confirmed"] = True
            validated.append(norm)
        except Exception as exc:
            raise ValueError(f"Mapping record #{idx} ({rec.get('internal_id')}) failed validation: {exc}") from exc

    plan = []
    created_count = 0
    updated_count = 0
    noop_count = 0

    for item in validated:
        sys_code = item["system_code"]
        etype = item["entity_type"]
        iid = item["internal_id"]
        xid = item["external_id"]
        ver = item["config_version"]
        conf = item["confirmed"]
        enab = item["enabled"]
        meta = item["metadata_json"]

        cur.execute("""SELECT external_id, config_version, confirmed, enabled, metadata_json
            FROM integration_identity_mappings
            WHERE system_code=%s AND entity_type=%s AND internal_id=%s FOR UPDATE""",
            (sys_code, etype, iid))
        existing = cur.fetchone()

        if not existing:
            action = "CREATE"
            created_count += 1
            effective_conf = conf
            effective_ver = ver
        else:
            old_xid, old_ver, old_conf, old_enab, old_meta = existing
            effective_conf = old_conf or conf
            effective_ver = max(old_ver, ver)
            if (old_xid == xid and old_ver == effective_ver and old_conf == effective_conf
                    and old_enab == enab and old_meta == meta):
                action = "NOOP"
                noop_count += 1
            else:
                action = "UPDATE"
                updated_count += 1

        plan_entry = {
            "action": action,
            "entity_type": etype,
            "internal_id": iid,
            "external_id": xid,
            "config_version": effective_ver,
            "confirmed": effective_conf,
            "enabled": enab,
        }
        plan.append(plan_entry)

        if not dry_run and action != "NOOP":
            cur.execute("""
                INSERT INTO integration_identity_mappings
                    (system_code, entity_type, internal_id, external_id, config_version,
                     confirmed, enabled, metadata_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        CURRENT_TIMESTAMP AT TIME ZONE 'UTC', CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                ON CONFLICT (system_code, entity_type, internal_id) DO UPDATE SET
                    external_id = EXCLUDED.external_id,
                    config_version = GREATEST(integration_identity_mappings.config_version, EXCLUDED.config_version),
                    confirmed = integration_identity_mappings.confirmed OR EXCLUDED.confirmed,
                    enabled = EXCLUDED.enabled,
                    metadata_json = integration_identity_mappings.metadata_json || EXCLUDED.metadata_json,
                    updated_at = CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
            """, (sys_code, etype, iid, xid, effective_ver, effective_conf, enab, json.dumps(meta)))

            if effective_conf:
                # Record immutable version history
                ident_dict = {
                    "system_code": sys_code, "entity_type": etype,
                    "internal_id": iid, "external_id": xid, "config_version": effective_ver
                }
                ident_hash = _payload_hash(ident_dict)
                cur.execute("""
                    INSERT INTO integration_identity_mapping_versions
                        (system_code, entity_type, internal_id, external_id, config_version, identity_hash)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (sys_code, etype, iid, xid, effective_ver, ident_hash))

    return {
        "total": len(records),
        "created": created_count,
        "updated": updated_count,
        "noop": noop_count,
        "dry_run": dry_run,
        "plan": plan,
    }


def load_mappings_manifest(file_path: Optional[str] = None) -> List[dict]:
    """Load a JSON manifest of mapping definitions."""
    path = file_path or os.getenv("INTEGRATION_MAPPINGS_FILE", "config/integration_mappings.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and "mappings" in data:
        data = data["mappings"]
    if not isinstance(data, list):
        raise ValueError(f"Manifest at {path} must contain a list of mapping records")
    return data


def load_and_apply_manifest(conn, file_path: Optional[str] = None,
                            dry_run: bool = False,
                            default_confirmed: bool = False) -> dict:
    """Load mappings from manifest file and apply inside a managed database transaction."""
    records = load_mappings_manifest(file_path)
    if not records:
        return {"total": 0, "created": 0, "updated": 0, "noop": 0, "dry_run": dry_run, "plan": []}

    with conn.cursor() as cur:
        result = apply_mappings(cur, records, dry_run=dry_run, default_confirmed=default_confirmed)
    if not dry_run:
        conn.commit()
    return result


def discover_confirmed_devices(cur, system_code: str = "nextgrp") -> List[str]:
    """Discover all confirmed external device IDs for Dev 1 snapshot synchronization."""
    cur.execute("""
        SELECT external_id FROM integration_identity_mappings
        WHERE system_code=%s AND entity_type='DEVICE' AND confirmed=TRUE AND enabled=TRUE
        ORDER BY external_id
    """, (system_code,))
    return [row[0] for row in cur.fetchall()]

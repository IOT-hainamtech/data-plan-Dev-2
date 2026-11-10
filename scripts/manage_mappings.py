#!/usr/bin/env python3
"""CLI utility to manage, validate, and apply Dev 1 -> Dev 2 identity mappings."""
import argparse
import json
import os
import sys

# Ensure app is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.database import db_connection
from integrations.mapping import (
    apply_mappings,
    discover_confirmed_devices,
    load_mappings_manifest,
    validate_mapping_record,
)


def list_mappings(conn, system_code="nextgrp"):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT entity_type, internal_id, external_id, config_version, confirmed, enabled, updated_at
            FROM integration_identity_mappings
            WHERE system_code=%s
            ORDER BY entity_type, internal_id
        """, (system_code,))
        rows = cur.fetchall()

    if not rows:
        print(f"No mappings found for system_code='{system_code}'.")
        return

    print(f"\n=== Current Integration Mappings ({system_code}) ===")
    print(f"{'ENTITY':<14} {'INTERNAL ID':<24} {'EXTERNAL ID':<26} {'VER':<5} {'CONF':<6} {'ENAB':<6} {'UPDATED AT'}")
    print("-" * 95)
    for etype, iid, xid, ver, conf, enab, upd in rows:
        conf_str = "YES" if conf else "NO"
        enab_str = "YES" if enab else "NO"
        upd_str = upd.isoformat() if upd else ""
        print(f"{etype:<14} {iid:<24} {xid:<26} {ver:<5} {conf_str:<6} {enab_str:<6} {upd_str}")
    print("-" * 95)
    print(f"Total: {len(rows)} mappings.\n")


def verify_mappings(conn, system_code="nextgrp"):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT entity_type, internal_id, external_id, config_version, confirmed, enabled, metadata_json
            FROM integration_identity_mappings
            WHERE system_code=%s AND confirmed=TRUE AND enabled=TRUE
            ORDER BY entity_type, internal_id
        """, (system_code,))
        rows = cur.fetchall()

    print(f"\n=== Verifying {len(rows)} Confirmed Mappings ({system_code}) ===")
    errors = 0
    with conn.cursor() as cur:
        for etype, iid, xid, ver, conf, enab, meta in rows:
            rec = {
                "system_code": system_code,
                "entity_type": etype,
                "internal_id": iid,
                "external_id": xid,
                "config_version": ver,
                "confirmed": conf,
                "enabled": enab,
                "metadata_json": meta or {},
            }
            try:
                validate_mapping_record(cur, rec, system_code=system_code)
                print(f"  [PASS] {etype:<12} {iid:<22} <-> {xid}")
            except Exception as exc:
                print(f"  [FAIL] {etype:<12} {iid:<22} <-> {xid}: {exc}")
                errors += 1

    if errors == 0:
        print("\nAll confirmed mappings PASSED verification.\n")
    else:
        print(f"\nWARNING: {errors} mapping(s) FAILED verification!\n")
    return errors == 0


def main():
    parser = argparse.ArgumentParser(description="Manage Dev 1 -> Dev 2 Integration Identity Mappings")
    parser.add_argument("--file", "-f", default=os.getenv("INTEGRATION_MAPPINGS_FILE", "config/integration_mappings.json"),
                        help="Path to mappings manifest JSON file")
    parser.add_argument("--system-code", default="nextgrp", help="System code (default: nextgrp)")
    parser.add_argument("--dry-run", action="store_true", help="Preview mapping actions without applying")
    parser.add_argument("--apply", action="store_true", help="Apply mappings from manifest into database")
    parser.add_argument("--confirm", action="store_true", help="Mark applied mappings as confirmed")
    parser.add_argument("--list", "-l", action="store_true", help="List current mappings in database")
    parser.add_argument("--verify", action="store_true", help="Verify all confirmed mappings against database entities")
    parser.add_argument("--devices", action="store_true", help="List confirmed devices for Dev 1 sync")

    args = parser.parse_args()

    if not any([args.apply, args.dry_run, args.list, args.verify, args.devices]):
        parser.print_help()
        sys.exit(0)

    with db_connection() as conn:
        if args.list:
            list_mappings(conn, system_code=args.system_code)

        if args.devices:
            with conn.cursor() as cur:
                devs = discover_confirmed_devices(cur, system_code=args.system_code)
            print(f"Confirmed devices ({len(devs)}): {', '.join(devs)}")

        if args.verify:
            ok = verify_mappings(conn, system_code=args.system_code)
            if not ok:
                sys.exit(1)

        if args.apply or args.dry_run:
            manifest_path = args.file
            if not os.path.exists(manifest_path):
                print(f"Error: Manifest file '{manifest_path}' not found.", file=sys.stderr)
                sys.exit(1)

            records = load_mappings_manifest(manifest_path)
            print(f"Loaded {len(records)} mapping record(s) from '{manifest_path}'.")

            with conn.cursor() as cur:
                result = apply_mappings(
                    cur,
                    records,
                    system_code=args.system_code,
                    dry_run=args.dry_run,
                    default_confirmed=args.confirm,
                )

            if not args.dry_run:
                conn.commit()
                print(f"SUCCESS: Applied {result['total']} mappings (created: {result['created']}, updated: {result['updated']}, unchanged: {result['noop']}).")
            else:
                print(f"DRY-RUN: Plan for {result['total']} mappings (would create: {result['created']}, would update: {result['updated']}, noop: {result['noop']}).")

            print("\nExecution Plan:")
            for p in result["plan"]:
                status = "CONFIRMED" if p["confirmed"] else "UNCONFIRMED"
                print(f"  [{p['action']:<6}] {p['entity_type']:<12} {p['internal_id']:<20} <-> {p['external_id']:<24} (v{p['config_version']}, {status})")


if __name__ == "__main__":
    main()

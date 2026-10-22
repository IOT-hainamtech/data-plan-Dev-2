"""Multi-tenant registry, ingestion, rule-scope and API isolation integration tests."""
import os
import unittest
import uuid
from datetime import datetime

from fastapi import HTTPException

from api import latest, list_devices
from core.database import db_connection
from ingestor import save_message


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class MultiSiteIntegrationTests(unittest.TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.org_a, self.org_b = f"orga-{suffix}", f"orgb-{suffix}"
        self.dev_a, self.dev_b = f"A-TH-{suffix}", f"B-TH-{suffix}"
        self.rule = f"PROFILE-HIGH-{suffix}"
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO organizations(org_code,display_name) VALUES(%s,%s)",
                                [(self.org_a, "Organization A"), (self.org_b, "Organization B")])
                cur.executemany("INSERT INTO sites(org_code,site_code,display_name) VALUES(%s,%s,%s)", [
                    (self.org_a, "site-1", "A Site 1"), (self.org_a, "site-2", "A Site 2"),
                    (self.org_b, "site-1", "B Site 1")])
                zones = [(org, site, zone, zone) for org, site in
                         ((self.org_a, "site-1"), (self.org_a, "site-2"), (self.org_b, "site-1"))
                         for zone in ("office", "entrance")]
                cur.executemany("INSERT INTO zones(org_code,site_code,zone_code,display_name) VALUES(%s,%s,%s,%s)", zones)
                cur.executemany("""INSERT INTO devices
                    (device_id,org_code,site_code,zone_code,profile_code,enabled)
                    VALUES(%s,%s,%s,'office','TEMP_HUM_SENSOR',TRUE)""",
                    [(self.dev_a, self.org_a, "site-1"), (self.dev_b, self.org_b, "site-1")])
                cur.execute("""INSERT INTO rule_definitions
                    (rule_code,device_id,rule_kind,metric,threshold_value,hysteresis_value,
                     hold_seconds,clear_hold_seconds,quiet_seconds,severity,enabled,
                     scope_type,scope_profile_code)
                    VALUES(%s,NULL,'THRESHOLD','temperature',20,0,0,0,0,'HIGH',TRUE,
                           'PROFILE','TEMP_HUM_SENSOR')""", (self.rule,))
            conn.commit()

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code=%s)", (self.rule,))
                cur.execute("DELETE FROM alarm_occurrences WHERE rule_code=%s", (self.rule,))
                cur.execute("DELETE FROM rule_runtime_state WHERE rule_code=%s", (self.rule,))
                cur.execute("DELETE FROM rule_definitions WHERE rule_code=%s", (self.rule,))
                cur.execute("DELETE FROM status_outbox WHERE device_id IN (%s,%s)", (self.dev_a, self.dev_b))
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE device_id IN (%s,%s))", (self.dev_a, self.dev_b))
                cur.execute("DELETE FROM alarm_occurrences WHERE device_id IN (%s,%s)", (self.dev_a, self.dev_b))
                cur.execute("DELETE FROM rule_runtime_state WHERE device_id IN (%s,%s)", (self.dev_a, self.dev_b))
                for table in ("telemetry", "telemetry_reports", "device_status_history", "device_status"):
                    cur.execute(f"DELETE FROM {table} WHERE device_id IN (%s,%s)", (self.dev_a, self.dev_b))
                cur.execute("DELETE FROM devices WHERE device_id IN (%s,%s)", (self.dev_a, self.dev_b))
                cur.execute("DELETE FROM zones WHERE org_code IN (%s,%s)", (self.org_a, self.org_b))
                cur.execute("DELETE FROM sites WHERE org_code IN (%s,%s)", (self.org_a, self.org_b))
                cur.execute("DELETE FROM organizations WHERE org_code IN (%s,%s)", (self.org_a, self.org_b))
            conn.commit()

    def publish(self, org, device, value):
        save_message(f"nw/v1/{org}/site-1/{device}/telemetry", {
            "schema_version": 1, "device_id": device, "boot_id": "multi-site-test",
            "seq": 1, "ts": datetime(2026, 10, 21, 1, 0).isoformat() + "Z",
            "quality": "good", "data": {"temperature": value}})

    def test_ingestion_runtime_and_api_are_isolated_by_organization(self):
        self.publish(self.org_a, self.dev_a, 25)
        self.publish(self.org_b, self.dev_b, 15)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT d.org_code,d.site_code,d.zone_code,t.device_id,t.value_number
                    FROM telemetry t JOIN devices d ON d.device_id=t.device_id
                    WHERE t.device_id IN (%s,%s) ORDER BY d.org_code""", (self.dev_a, self.dev_b))
                rows = cur.fetchall()
                self.assertEqual({row[0] for row in rows}, {self.org_a, self.org_b})
                cur.execute("SELECT device_id FROM rule_runtime_state WHERE rule_code=%s", (self.rule,))
                self.assertEqual({row[0] for row in cur.fetchall()}, {self.dev_a, self.dev_b})
                cur.execute("SELECT device_id FROM alarm_occurrences WHERE rule_code=%s", (self.rule,))
                self.assertEqual([row[0] for row in cur.fetchall()], [self.dev_a])

        visible = list_devices(self.org_a)["devices"]
        self.assertIn(self.dev_a, {d["device_id"] for d in visible})
        self.assertNotIn(self.dev_b, {d["device_id"] for d in visible})
        with self.assertRaises(HTTPException) as denied:
            latest(self.dev_b, self.org_a)
        self.assertEqual(denied.exception.status_code, 404)

    def test_wrong_topic_tenant_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "wrongly routed"):
            self.publish(self.org_a, self.dev_b, 25)


if __name__ == "__main__":
    unittest.main()

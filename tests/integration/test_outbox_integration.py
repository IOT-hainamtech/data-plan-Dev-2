import os
import unittest
import uuid
from datetime import datetime, timedelta

from core.database import db_connection
from integrations.nextgrp import DeliveryResult
from outbox.worker import OutboxStore, OutboxWorker


class Adapter:
    def __init__(self, error=None, duplicate=False):
        self.error, self.duplicate = error, duplicate

    def send(self, key, payload):
        if self.error:
            raise self.error
        return DeliveryResult(200, {"accepted": True, "duplicate": self.duplicate,
                                    "alarm_key": key, "ack_id": "integration-ack"})


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class OutboxDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.key = f"test-outbox-{uuid.uuid4().hex}"
        self.status_event_ids = []
        self.created_rule_history = False
        self.at = datetime(2035, 1, 1)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT org_code FROM devices WHERE device_id='TH-001'")
                self.org = cur.fetchone()[0]
                cur.execute("SELECT config_version FROM rule_definitions WHERE rule_code='S1_TEMP_HIGH'")
                self.rule_version = cur.fetchone()[0]
                self.mapping_keys = (("ORGANIZATION", self.org), ("DEVICE", "TH-001"),
                                     ("RULE", "S1_TEMP_HIGH"))
                for entity_type, internal_id in self.mapping_keys:
                    cur.execute("""INSERT INTO integration_identity_mappings
                        (system_code,entity_type,internal_id,external_id,config_version,
                         confirmed,enabled) VALUES ('nextgrp',%s,%s,%s,%s,TRUE,TRUE)
                        ON CONFLICT (system_code,entity_type,internal_id) DO NOTHING""",
                        (entity_type, internal_id, f"TEST-{entity_type}-{uuid.uuid4().hex}",
                         self.rule_version if entity_type == "RULE" else 0))
                cur.execute("""INSERT INTO integration_identity_mapping_versions
                    (system_code,entity_type,internal_id,external_id,config_version,identity_hash)
                    SELECT system_code,entity_type,internal_id,external_id,config_version,%s
                    FROM integration_identity_mappings
                    WHERE system_code='nextgrp' AND entity_type='RULE'
                      AND internal_id='S1_TEMP_HIGH'
                    ON CONFLICT DO NOTHING RETURNING 1""",("f" * 64,))
                self.created_rule_history = cur.fetchone() is not None
                cur.execute("""INSERT INTO alarm_occurrences
                    (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,
                     event_type,triggered_at,payload_json)
                    VALUES (%s,%s,'TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH',
                            'OUTBOX_TEST',%s,%s::jsonb)""",
                    (self.key, self.key, self.at,
                     '{"alarm_key":"' + self.key + '","rule_version":' +
                     str(self.rule_version) + '}'))
                cur.execute("""INSERT INTO alarm_outbox
                    (alarm_key,payload_json,next_attempt_at,created_at)
                    VALUES (%s,%s::jsonb,%s,%s)""",
                    (self.key, '{"alarm_key":"' + self.key + '"}', self.at, self.at))
                cur.execute("SELECT id FROM alarm_outbox WHERE alarm_key=%s", (self.key,))
                self.row_id = cur.fetchone()[0]
            conn.commit()
        self.store = OutboxStore(alarm_key=self.key)

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                if self.status_event_ids:
                    cur.execute("DELETE FROM status_outbox WHERE delivery_event_id=ANY(%s)",
                                (self.status_event_ids,))
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key=%s", (self.key,))
                cur.execute("DELETE FROM alarm_occurrences WHERE alarm_key=%s", (self.key,))
                if self.created_rule_history:
                    cur.execute("""DELETE FROM integration_identity_mapping_versions
                        WHERE system_code='nextgrp' AND entity_type='RULE'
                          AND internal_id='S1_TEMP_HIGH' AND config_version=%s
                          AND identity_hash=%s""",(self.rule_version,"f" * 64))
                for entity_type, internal_id in self.mapping_keys:
                    cur.execute("""DELETE FROM integration_identity_mappings
                        WHERE system_code='nextgrp' AND entity_type=%s AND internal_id=%s
                          AND left(external_id,5)='TEST-'""", (entity_type, internal_id))
            conn.commit()

    def state(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT delivery_state,attempt_count,failure_count,ack_id,
                    locked_by FROM alarm_outbox WHERE alarm_key=%s""", (self.key,))
                return cur.fetchone()

    def test_success_requires_valid_ack(self):
        OutboxWorker(self.store, Adapter(duplicate=True), worker_id="integration").run_once(self.at)
        self.assertEqual(self.state(), ("DELIVERED", 1, 0, "integration-ack", None))

    def test_network_failure_schedules_retry(self):
        OutboxWorker(self.store, Adapter(error=OSError("offline")), worker_id="integration",
                     jitter_ratio=0).run_once(self.at)
        state = self.state()
        self.assertEqual(state[:3], ("PENDING", 1, 1))
        self.assertIsNone(state[3])

    def test_retry_limit_moves_row_to_failed(self):
        OutboxWorker(self.store, Adapter(error=TimeoutError("timeout")),
                     worker_id="integration", max_attempts=1,
                     jitter_ratio=0).run_once(self.at)
        self.assertEqual(self.state()[:3], ("FAILED", 1, 1))

    def test_http_500_schedules_retry(self):
        class Http500Adapter:
            def send(self, key, payload):
                return DeliveryResult(500, {"error": "Internal Server Error"})
        OutboxWorker(self.store, Http500Adapter(), worker_id="integration", jitter_ratio=0).run_once(self.at)
        state = self.state()
        self.assertEqual(state[:3], ("PENDING", 1, 1))

    def test_invalid_ack_body_schedules_retry(self):
        class BadAckAdapter:
            def send(self, key, payload):
                return DeliveryResult(200, {"accepted": False, "alarm_key": key})
        OutboxWorker(self.store, BadAckAdapter(), worker_id="integration", jitter_ratio=0).run_once(self.at)
        state = self.state()
        self.assertEqual(state[:3], ("PENDING", 1, 1))

    def test_expired_lease_is_reclaimed_after_restart(self):
        claimed = self.store.claim("dead-worker", 1, 1, self.at)
        self.assertEqual(len(claimed), 1)
        OutboxWorker(self.store, Adapter(), worker_id="restarted-worker").run_once(
            self.at + timedelta(seconds=2))
        self.assertEqual(self.state(), ("DELIVERED", 2, 0, "integration-ack", None))

    def test_armed_scope_blocks_old_and_wrong_rows_but_allows_new_frozen_payload(self):
        old = OutboxStore(min_id_exclusive=self.row_id, not_before=self.at,
            approved_org=self.org, approved_devices=("TH-001", "DR-001"),
            approved_rules=("S1_TEMP_HIGH", "S6_DOOR_OPEN"))
        self.assertEqual(old.claim("armed-old", 10, 60, self.at), [])
        wrong_device = OutboxStore(min_id_exclusive=self.row_id - 1, not_before=self.at,
            approved_org=self.org, approved_devices=("DR-001",),
            approved_rules=("S1_TEMP_HIGH",))
        self.assertEqual(wrong_device.claim("armed-wrong", 10, 60, self.at), [])
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE integration_identity_mappings SET confirmed=FALSE
                    WHERE system_code='nextgrp' AND entity_type='RULE'
                      AND internal_id='S1_TEMP_HIGH'""")
            conn.commit()
        # Migration 019 preserves the mapping version that was confirmed when
        # the episode was created. Revoking the current pointer must not erase
        # that immutable provenance, but the explicit arm scope still applies.
        frozen_history = OutboxStore(min_id_exclusive=self.row_id - 1, not_before=self.at,
            approved_org=self.org, approved_devices=("TH-001", "DR-001"),
            approved_rules=("S1_TEMP_HIGH", "S6_DOOR_OPEN"))
        historical_claim=frozen_history.claim("armed-history", 10, 60, self.at)
        self.assertEqual([row[0] for row in historical_claim],[self.row_id])
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE alarm_outbox SET delivery_state='PENDING',locked_by=NULL,
                    locked_until=NULL,attempt_count=0 WHERE id=%s""",(self.row_id,))
                cur.execute("""UPDATE integration_identity_mappings SET confirmed=TRUE
                    WHERE system_code='nextgrp' AND entity_type='RULE'
                      AND internal_id='S1_TEMP_HIGH'""")
                cur.execute("UPDATE alarm_outbox SET payload_json=%s::jsonb WHERE id=%s",
                            ('{"alarm_key":"NW-IT-RULE-S1:NW-IT-TH001:stable"}', self.row_id))
            conn.commit()
        valid = OutboxStore(min_id_exclusive=self.row_id - 1, not_before=self.at,
            approved_org=self.org, approved_devices=("TH-001", "DR-001"),
            approved_rules=("S1_TEMP_HIGH", "S6_DOOR_OPEN"))
        claimed = valid.claim("armed-valid", 10, 60, self.at)
        self.assertEqual([row[0] for row in claimed], [self.row_id])

    def test_status_arm_excludes_backlog_and_reconciliation_snapshot(self):
        self.status_event_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO status_outbox
                    (delivery_event_id,device_id,payload_json,next_attempt_at,created_at)
                    VALUES (%s,'TH-001',%s::jsonb,%s,%s) RETURNING id""",
                    (self.status_event_ids[0],
                     '{"org_code":"' + self.org + '","transition":true}', self.at, self.at))
                high_water = cur.fetchone()[0]
                cur.execute("""INSERT INTO status_outbox
                    (delivery_event_id,device_id,payload_json,next_attempt_at,created_at)
                    VALUES (%s,'TH-001',%s::jsonb,%s,%s),
                           (%s,'TH-001',%s::jsonb,%s,%s)""",
                    (self.status_event_ids[1],
                     '{"org_code":"' + self.org + '","transition":false}', self.at, self.at,
                     self.status_event_ids[2],
                     '{"org_code":"' + self.org + '","transition":true}', self.at, self.at))
            conn.commit()
        store = OutboxStore(table="status_outbox", key_column="delivery_event_id",
            min_id_exclusive=high_water, not_before=self.at, approved_org=self.org,
            approved_devices=("TH-001", "DR-001"))
        claimed = store.claim("realtime-status", 10, 60, self.at)
        self.assertEqual([str(row[1]) for row in claimed], [self.status_event_ids[2]])

    def test_two_workers_concurrent_claim(self):
        # Create second alarm row
        key2 = f"{self.key}-second"
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO alarm_occurrences
                    (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,
                     event_type,triggered_at,payload_json)
                    VALUES (%s,%s,'TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH',
                            'OUTBOX_TEST',%s,%s::jsonb)""",
                    (key2, key2, self.at, '{"alarm_key":"' + key2 + '"}'))
                cur.execute("""INSERT INTO alarm_outbox
                    (alarm_key,payload_json,next_attempt_at,created_at)
                    VALUES (%s,%s::jsonb,%s,%s)""",
                    (key2, '{"alarm_key":"' + key2 + '"}', self.at, self.at))
            conn.commit()

        try:
            store_all = OutboxStore()
            # Worker 1 claims 1 row
            w1_claims = store_all.claim("worker-1", 1, 60, self.at)
            # Worker 2 claims 1 row concurrently
            w2_claims = store_all.claim("worker-2", 1, 60, self.at)

            self.assertEqual(len(w1_claims), 1)
            self.assertEqual(len(w2_claims), 1)
            # Both workers received different alarm keys
            self.assertNotEqual(w1_claims[0][1], w2_claims[0][1])
        finally:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM alarm_outbox WHERE alarm_key=%s", (key2,))
                    cur.execute("DELETE FROM alarm_occurrences WHERE alarm_key=%s", (key2,))
                conn.commit()


if __name__ == "__main__":
    unittest.main()

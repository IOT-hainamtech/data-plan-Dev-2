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
        self.at = datetime(2035, 1, 1)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO alarm_occurrences
                    (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,
                     event_type,triggered_at,payload_json)
                    VALUES (%s,%s,'TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH',
                            'OUTBOX_TEST',%s,%s::jsonb)""",
                    (self.key, self.key, self.at, '{"alarm_key":"' + self.key + '"}'))
                cur.execute("""INSERT INTO alarm_outbox
                    (alarm_key,payload_json,next_attempt_at,created_at)
                    VALUES (%s,%s::jsonb,%s,%s)""",
                    (self.key, '{"alarm_key":"' + self.key + '"}', self.at, self.at))
            conn.commit()
        self.store = OutboxStore(alarm_key=self.key)

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key=%s", (self.key,))
                cur.execute("DELETE FROM alarm_occurrences WHERE alarm_key=%s", (self.key,))
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

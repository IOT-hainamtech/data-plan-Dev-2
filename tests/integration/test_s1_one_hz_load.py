"""Opt-in isolated PostgreSQL capacity check for the S1 one-hertz path."""
import os
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from core.database import db_connection
from ingestor import save_message


@unittest.skipUnless(
    os.getenv("RUN_DB_TESTS") == "1" and os.getenv("RUN_S1_1HZ_LOAD_TEST") == "1",
    "set RUN_DB_TESTS=1 and RUN_S1_1HZ_LOAD_TEST=1 on an isolated database",
)
class S1OneHertzLoadTests(unittest.TestCase):
    sample_count = 120

    def setUp(self):
        self.boot_id = f"s1-1hz-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM device_event_outbox WHERE event_id LIKE %s",
                            (f"TH-001-{self.boot_id}-%",))
                cur.execute("DELETE FROM telemetry WHERE device_id='TH-001' AND boot_id=%s",
                            (self.boot_id,))
                cur.execute("DELETE FROM telemetry_reports WHERE device_id='TH-001' AND boot_id=%s",
                            (self.boot_id,))
            conn.commit()

    def test_ingestion_and_realtime_outbox_have_one_hertz_headroom(self):
        first = datetime.now(timezone.utc) - timedelta(seconds=self.sample_count)
        started = time.perf_counter()
        with mock.patch("ingestor.on_telemetry"):
            for seq in range(self.sample_count):
                stamp = (first + timedelta(seconds=seq)).isoformat().replace("+00:00", "Z")
                save_message("nw/v1/demo/demo-site/TH-001/telemetry", {
                    "schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_id,
                    "seq": seq, "ts": stamp, "ts_source": "ntp", "quality": "good",
                    "data": {"temperature": 28.0 + (seq % 5) / 100.0, "humidity": 60.0},
                })
        elapsed = time.perf_counter() - started
        rate = self.sample_count / elapsed

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM telemetry_reports WHERE boot_id=%s",
                            (self.boot_id,))
                reports = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM telemetry WHERE boot_id=%s", (self.boot_id,))
                metrics = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM device_event_outbox WHERE event_id LIKE %s",
                            (f"TH-001-{self.boot_id}-%",))
                outbound = cur.fetchone()[0]

        print(f"S1_1HZ_LOAD samples={self.sample_count} elapsed_s={elapsed:.3f} rate={rate:.2f}/s")
        self.assertEqual((reports, metrics, outbound),
                         (self.sample_count, self.sample_count * 2, self.sample_count))
        # Require at least 2x the target input rate so a single slow commit does
        # not immediately create an ever-growing MQTT/DB queue.
        self.assertGreaterEqual(rate, 2.0)


if __name__ == "__main__":
    unittest.main()

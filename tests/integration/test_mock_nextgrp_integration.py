import os
import unittest
import uuid

from integrations.nextgrp import NextGrpAdapter
from outbox.worker import validate_ack


@unittest.skipUnless(os.getenv("RUN_HTTP_TESTS") == "1", "set RUN_HTTP_TESTS=1")
class MockNextGrpTests(unittest.TestCase):
    def test_duplicate_request_returns_same_valid_ack(self):
        key = f"mock-http-test-{uuid.uuid4().hex}"
        adapter = NextGrpAdapter(
            os.environ["MOCK_TEST_URL"], 2, "bearer", os.environ["MOCK_TEST_TOKEN"],
            allow_insecure_mock=True)
        first = adapter.send(key, {"alarm_key": key})
        second = adapter.send(key, {"alarm_key": key})
        self.assertTrue(validate_ack(first.body, key))
        self.assertTrue(validate_ack(second.body, key))
        self.assertFalse(first.body["duplicate"])
        self.assertTrue(second.body["duplicate"])
        self.assertEqual(first.body["ack_id"], second.body["ack_id"])


if __name__ == "__main__":
    unittest.main()

"""Security regressions for authenticated, tenant-scoped operational endpoints."""
import unittest
from unittest.mock import patch

from api import application


class _Cursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return 0, 0, 0, None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self, **_):
        return self._cursor


class OutboxMetricsSecurityTests(unittest.TestCase):
    def test_metrics_query_is_scoped_to_authenticated_organization(self):
        cursor = _Cursor()
        with patch.object(application, "db_connection", return_value=_Connection(cursor)):
            body = application.outbox_metrics(org_code="tenant-a")

        self.assertIn("JOIN alarm_occurrences", cursor.sql)
        self.assertIn("JOIN devices", cursor.sql)
        self.assertIn("WHERE d.org_code=%s", cursor.sql)
        self.assertEqual(cursor.params, ("tenant-a",))
        self.assertEqual(body, {
            "pending": 0,
            "failed": 0,
            "delivery_failures": 0,
            "oldest_pending_age_seconds": 0.0,
        })


if __name__ == "__main__":
    unittest.main()

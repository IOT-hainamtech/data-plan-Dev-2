import os
import unittest

from api.application import latest, list_alarms, outbox_metrics
from core.auth import service_context


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "requires isolated PostgreSQL")
class OrgSiteCutoverApiTests(unittest.TestCase):
    def test_new_org_authorization_and_device_registry(self):
        token = os.environ["DP_INBOUND_TOKEN"]
        org = service_context(authorization=f"Bearer {token}",
                              x_org_code="NW-LOCAL-ORG-001")
        self.assertEqual(org, "NW-LOCAL-ORG-001")
        self.assertEqual(latest("TH-001", org)["organization"], org)
        self.assertEqual(latest("DR-001", org)["organization"], org)

    def test_historical_alarm_and_outbox_tenant_are_immutable(self):
        old = list_alarms(limit=50, offset=0, org_code="hainam")
        new = list_alarms(limit=50, offset=0, org_code="NW-LOCAL-ORG-001")
        self.assertEqual(old["total"], 1)
        self.assertEqual(new["total"], 0)
        self.assertEqual(outbox_metrics("hainam")["pending"], 1)
        self.assertEqual(outbox_metrics("NW-LOCAL-ORG-001")["pending"], 0)


if __name__ == "__main__":
    unittest.main()

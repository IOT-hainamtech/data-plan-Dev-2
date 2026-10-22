"""Contract-v1.3 proposed integration tests against real PostgreSQL and live API/Mock Bridge."""
import copy,json,os,unittest,urllib.error,urllib.request,uuid

from core.database import db_connection
from integrations.nextgrp import NextGrpAdapter
from integrations.snapshot import reconcile_snapshot

@unittest.skipUnless(os.getenv("RUN_DEV1_TESTS")=="1","set RUN_DEV1_TESTS=1")
class Dev1V13IntegrationTests(unittest.TestCase):
    org="v13-test"; site="hq"; zone="lab"; profile="V13_TEMP"; device="V13-TH-001"; rule="V13-S1"
    base=os.getenv("DATA_API_URL","http://data-api:8000")
    token=os.getenv("DP_TEST_TOKEN","mock-data-plane-inbound-token")
    bridge=os.getenv("BRIDGE_TEST_URL","http://mock-nextgrp:8080")

    @classmethod
    def request(cls,method,path,body=None,org=None,token=None):
        headers={"Content-Type":"application/json"}
        if token is not False: headers["Authorization"]=f"Bearer {token or cls.token}"
        if org is not False: headers["X-Org-Code"]=org or cls.org
        req=urllib.request.Request(cls.base+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=8) as response: return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc: return exc.code,json.loads(exc.read())

    @classmethod
    def setUpClass(cls):
        contract_path=os.getenv("DEV1_CONTRACT_PATH","/app/contracts/nextwireless_m1_contract_v1_3.json")
        if not os.path.exists(contract_path):
            contract_path=os.path.join(os.path.dirname(__file__),"..","..","docs","contracts","nextwireless_m1_contract_v1_3.json")
        with open(contract_path,encoding="utf-8") as handle: examples=json.load(handle)["examples"]
        cls.org_body=copy.deepcopy(examples["01_organization"]);cls.org_body.update(org_code=cls.org,name="V13 Test")
        cls.site_body=copy.deepcopy(examples["02_site"]);cls.site_body.update(org_code=cls.org,site_code=cls.site,name="HQ")
        cls.zone_body=copy.deepcopy(examples["03_zone"]);cls.zone_body.update(org_code=cls.org,site_code=cls.site,zone_code=cls.zone,name="Lab")
        cls.profile_body=copy.deepcopy(examples["05_profile_s1"]);cls.profile_body["profile_code"]=cls.profile
        cls.device_body=copy.deepcopy(examples["07_device_s1"]);cls.device_body.update(
            device_id=cls.device,org_code=cls.org,site_code=cls.site,zone_code=cls.zone,profile_code=cls.profile,
            connectivity_type="5G",credential_ref="secretref://v13/device")
        cls.rule_body=copy.deepcopy(examples["09_rule_s1"]);cls.rule_body.update(rule_id=cls.rule,org_code=cls.org)
        cls.rule_body["scope"]["ref"]=cls.device
        cls.alarm_state_body=copy.deepcopy(examples["14_alarm_state"]);cls.alarm_state_body["org_code"]=cls.org

    @classmethod
    def tearDownClass(cls):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_lifecycle WHERE org_code=%s",(cls.org,))
                cur.execute("DELETE FROM alarm_episode_mappings WHERE device_id=%s",(cls.device,))
                cur.execute("DELETE FROM rule_runtime_state WHERE rule_code=%s",(cls.rule,))
                cur.execute("DELETE FROM rule_definitions WHERE rule_code=%s",(cls.rule,))
                cur.execute("DELETE FROM status_outbox WHERE device_id=%s",(cls.device,))
                cur.execute("DELETE FROM devices WHERE device_id=%s",(cls.device,))
                cur.execute("DELETE FROM device_profile_events WHERE profile_code=%s",(cls.profile,))
                cur.execute("DELETE FROM device_profile_points WHERE profile_code=%s",(cls.profile,))
                cur.execute("DELETE FROM device_profiles WHERE profile_code=%s",(cls.profile,))
                cur.execute("DELETE FROM zones WHERE org_code=%s",(cls.org,));cur.execute("DELETE FROM sites WHERE org_code=%s",(cls.org,));cur.execute("DELETE FROM organizations WHERE org_code=%s",(cls.org,))
            conn.commit()

    def test_01_authentication_and_tenant_scope(self):
        self.assertEqual(self.request("GET","/api/v1/health",org=False,token=False)[0],200)
        self.assertEqual(self.request("GET","/api/v1/devices",token=False)[0],401)
        self.assertEqual(self.request("PUT",f"/api/v1/sync/organizations/{self.org}",self.org_body,org="not-allowed")[0],403)

    def test_02_ordered_sync_and_relationships(self):
        for path,body in [(f"/api/v1/sync/organizations/{self.org}",self.org_body),(f"/api/v1/sync/sites/{self.org}/{self.site}",self.site_body),
          (f"/api/v1/sync/zones/{self.org}/{self.site}/{self.zone}",self.zone_body),(f"/api/v1/sync/profiles/{self.profile}",self.profile_body),
          (f"/api/v1/sync/devices/{self.device}",self.device_body),(f"/api/v1/sync/rules/{self.rule}",self.rule_body)]:
            status,result=self.request("PUT",path,body); self.assertEqual(status,200,result); self.assertFalse(result["duplicate"])

    def test_03_version_duplicate_stale_and_conflict(self):
        status,result=self.request("PUT",f"/api/v1/sync/devices/{self.device}",self.device_body);self.assertEqual((status,result["duplicate"]),(200,True))
        changed={**self.device_body,"connectivity_type":"LAN"};self.assertEqual(self.request("PUT",f"/api/v1/sync/devices/{self.device}",changed)[0],409)
        upgraded={**self.device_body,"config_version":2,"connectivity_type":"LAN"};self.assertEqual(self.request("PUT",f"/api/v1/sync/devices/{self.device}",upgraded)[0],200)
        self.assertEqual(self.request("PUT",f"/api/v1/sync/devices/{self.device}",self.device_body)[0],409)

    def test_04_url_identity_and_invalid_relationship(self):
        self.assertEqual(self.request("PUT","/api/v1/sync/devices/WRONG",self.device_body)[0],422)
        bad={**self.device_body,"config_version":3,"zone_code":"missing"};self.assertEqual(self.request("PUT",f"/api/v1/sync/devices/{self.device}",bad)[0],422)

    def test_05_data_api_and_zone_scope(self):
        self.assertEqual(self.request("GET","/api/v1/devices")[0],200)
        status,body=self.request("GET",f"/api/v1/zones/{self.zone}/summary?site_code={self.site}");self.assertEqual(status,200);self.assertEqual(body["device_count"],1)
        self.assertEqual(self.request("GET","/api/v1/devices",org="demo")[0],200)

    def test_06_alarm_state_monotonic(self):
        wire=f"{self.rule}:{self.device}:1700000000"
        with db_connection() as conn:
            with conn.cursor() as cur: cur.execute("INSERT INTO alarm_episode_mappings VALUES(%s,%s,now() at time zone 'utc',%s,%s) ON CONFLICT DO NOTHING",("ep:v13",wire,self.rule,self.device))
            conn.commit()
        body={**self.alarm_state_body,"alarm_key":wire,"changed_by":"user:1"}
        self.assertEqual(self.request("POST",f"/api/v1/alarms/{wire}/state",body)[0],200)
        self.assertTrue(self.request("POST",f"/api/v1/alarms/{wire}/state",body)[1]["duplicate"])
        reverse={**body,"version":2,"state":"acknowledged"};self.assertEqual(self.request("POST",f"/api/v1/alarms/{wire}/state",reverse)[0],200)
        self.assertEqual(self.request("POST",f"/api/v1/alarms/{wire}/state",{**body,"version":3,"state":"closed"})[0],200)
        self.assertEqual(self.request("POST",f"/api/v1/alarms/{wire}/state",{**body,"version":4,"state":"in_progress"})[0],409)

    def test_07_bridge_202_and_duplicate_ack(self):
        adapter=NextGrpAdapter(self.bridge+"/internal/events/alarm",5,token="mock-development-token",allow_insecure_mock=True,require_202=True)
        event=str(uuid.uuid4());payload={"event_id":event,"alarm_key":"wire:test","episode_key":"wire:test"}
        first=adapter.send("ignored",payload);second=adapter.send("ignored",payload)
        self.assertEqual(first.status,202);self.assertTrue(first.body["accepted"]);self.assertTrue(second.body["duplicate"]);self.assertEqual(first.body["ack_id"],second.body["ack_id"])

    def test_08_snapshot_order_and_stale_protection(self):
        device={**self.device_body,"config_version":2,"connectivity_type":"LAN"}
        resources=[]
        for kind,key,body,version_field in [
            ("rule",self.rule,self.rule_body,"version"),("device",self.device,device,"config_version"),
            ("profile",self.profile,self.profile_body,"version"),("zone",f"{self.org}/{self.site}/{self.zone}",self.zone_body,"version"),
            ("site",f"{self.org}/{self.site}",self.site_body,"version"),("organization",self.org,self.org_body,"version")]:
            payload=copy.deepcopy(body);version=payload.pop(version_field)
            resources.append({"kind":kind,"key":key,"version":version,"payload":payload})
        snap={"contract_version":"1.3-proposed","org_code":self.org,"resources":resources}
        result=reconcile_snapshot(snap,{self.org});self.assertEqual(len(result),6);self.assertTrue(all(item["duplicate"] for item in result))
        forbidden={**self.org_body,"org_code":"forbidden"};forbidden.pop("version")
        with self.assertRaises(PermissionError): reconcile_snapshot({"contract_version":"1.3-proposed","resources":[{"kind":"organization","version":1,"payload":forbidden}]},{self.org})

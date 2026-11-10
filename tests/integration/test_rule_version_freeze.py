"""Isolated PostgreSQL coverage for IF-03 atomic versioning and episode freeze."""
import os
import unittest
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException

from core.database import db_connection
from registry.sync import apply_rule
from rules.engine import evaluate_due_rules, on_event, on_telemetry


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class RuleVersionFreezeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.suffix=uuid.uuid4().hex[:10]
        self.org=f"freeze-{self.suffix}"
        self.site="hq"; self.zone="lab"; self.profile=f"PROFILE-{self.suffix}"
        self.th=f"TH-{self.suffix}"; self.dr=f"DR-{self.suffix}"
        self.s1=f"S1_FREEZE_{self.suffix}"; self.s6=f"S6_FREEZE_{self.suffix}"
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO organizations(org_code,display_name) VALUES(%s,%s)",(self.org,self.org))
                cur.execute("INSERT INTO sites(org_code,site_code,display_name,timezone) VALUES(%s,%s,%s,'Asia/Ho_Chi_Minh')",(self.org,self.site,self.site))
                cur.execute("INSERT INTO zones(org_code,site_code,zone_code,display_name) VALUES(%s,%s,%s,%s)",(self.org,self.site,self.zone,self.zone))
                cur.execute("INSERT INTO device_profiles(profile_code,display_name) VALUES(%s,%s)",(self.profile,self.profile))
                for device in (self.th,self.dr):
                    cur.execute("""INSERT INTO devices(device_id,org_code,site_code,zone_code,profile_code,
                        telemetry_interval_seconds,heartbeat_interval_seconds)
                        VALUES(%s,%s,%s,%s,%s,1,60)""",
                        (device,self.org,self.site,self.zone,self.profile))
                mappings=[("ORGANIZATION",self.org,f"NW-{self.org}",0),
                    ("DEVICE",self.th,f"NW-{self.th}",0),("DEVICE",self.dr,f"NW-{self.dr}",0),
                    ("RULE",self.s1,f"NW-{self.s1}",4),("RULE",self.s6,f"NW-{self.s6}",3)]
                for entity,internal,external,version in mappings:
                    cur.execute("""INSERT INTO integration_identity_mappings
                        (system_code,entity_type,internal_id,external_id,config_version,confirmed,enabled)
                        VALUES('nextgrp',%s,%s,%s,%s,TRUE,TRUE)""",
                        (entity,internal,external,version))
                apply_rule(cur,self.s1_data(4,30),sync_mapping=True)
                apply_rule(cur,self.s6_data(3,300),sync_mapping=True)
            conn.commit()

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE device_id=ANY(%s))",([self.th,self.dr],))
                cur.execute("DELETE FROM alarm_occurrences WHERE device_id=ANY(%s)",([self.th,self.dr],))
                cur.execute("DELETE FROM alarm_episode_mappings WHERE device_id=ANY(%s)",([self.th,self.dr],))
                cur.execute("DELETE FROM rule_runtime_state WHERE device_id=ANY(%s)",([self.th,self.dr],))
                cur.execute("DELETE FROM integration_identity_mapping_versions WHERE internal_id=ANY(%s)",([self.s1,self.s6],))
                cur.execute("DELETE FROM integration_identity_mappings WHERE internal_id=ANY(%s)",([self.org,self.th,self.dr,self.s1,self.s6],))
                cur.execute("DELETE FROM rule_definitions WHERE rule_code=ANY(%s)",([self.s1,self.s6],))
                cur.execute("DELETE FROM devices WHERE device_id=ANY(%s)",([self.th,self.dr],))
                cur.execute("DELETE FROM device_profiles WHERE profile_code=%s",(self.profile,))
                cur.execute("DELETE FROM zones WHERE org_code=%s",(self.org,))
                cur.execute("DELETE FROM sites WHERE org_code=%s",(self.org,))
                cur.execute("DELETE FROM organizations WHERE org_code=%s",(self.org,))
            conn.commit()

    def s1_data(self,version,threshold):
        return {"rule_id":self.s1,"version":version,"org_code":self.org,
            "rule_kind":"THRESHOLD","scope":{"type":"DEVICE","device_id":self.th},
            "metric":"temperature","operator":">","threshold":threshold,"duration_s":0,
            "hysteresis":0,"clear_duration_s":0,"quiet_window_s":0,"event_type":None,
            "open_limit_s":None,"business_start":None,"business_end":None,"timezone":None,
            "severity":"High","auto_close":True,"enabled":True}

    def s6_data(self,version,hold):
        return {"rule_id":self.s6,"version":version,"org_code":self.org,
            "rule_kind":"STATE_CHANGE","scope":{"type":"DEVICE","device_id":self.dr},
            "metric":None,"operator":None,"threshold":None,"duration_s":0,"hysteresis":0,
            "clear_duration_s":0,"quiet_window_s":0,"event_type":"DOOR_OPENED",
            "open_limit_s":hold,"business_start":None,"business_end":None,"timezone":None,
            "severity":"High","auto_close":True,"enabled":True}

    def assert_frozen_delivery_pair(self,cur,device,version):
        cur.execute("""SELECT payload_json->>'kind',payload_json->>'rule_version'
            FROM alarm_outbox
            WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE device_id=%s)
            ORDER BY id""",(device,))
        self.assertEqual(cur.fetchall(),[("occurrence",str(version)),("cleared",str(version))])

    def test_atomic_rule_and_mapping_version_with_conflict_rollback(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                result=apply_rule(cur,self.s1_data(5,31),sync_mapping=True)
                self.assertEqual(result["applied_version"],5)
                cur.execute("SELECT config_version FROM integration_identity_mappings WHERE entity_type='RULE' AND internal_id=%s",(self.s1,))
                self.assertEqual(cur.fetchone()[0],5)
                cur.execute("SELECT count(*) FROM integration_identity_mapping_versions WHERE internal_id=%s AND config_version IN (4,5)",(self.s1,))
                self.assertEqual(cur.fetchone()[0],2)
            conn.commit()
        with db_connection() as conn:
            with conn.cursor() as cur:
                duplicate=apply_rule(cur,self.s1_data(5,31),sync_mapping=True)
                self.assertTrue(duplicate["duplicate"])
                with self.assertRaises(HTTPException) as stale:
                    apply_rule(cur,self.s1_data(4,30),sync_mapping=True)
                self.assertEqual(stale.exception.detail["code"],"STALE_VERSION")
            conn.rollback()
        with self.assertRaises(HTTPException):
            with db_connection() as conn:
                with conn.cursor() as cur:
                    apply_rule(cur,self.s1_data(5,32),sync_mapping=True)
                conn.commit()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE integration_identity_mappings SET confirmed=FALSE WHERE entity_type='RULE' AND internal_id=%s",(self.s1,))
            conn.commit()
        with self.assertRaises(HTTPException) as missing:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    apply_rule(cur,self.s1_data(6,33),sync_mapping=True)
                conn.commit()
        self.assertEqual(missing.exception.detail["code"],"MAPPING_NOT_CONFIRMED")
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT threshold_value,config_version FROM rule_definitions WHERE rule_code=%s",(self.s1,))
                self.assertEqual(cur.fetchone(),(31.0,5))
                cur.execute("UPDATE integration_identity_mappings SET confirmed=TRUE WHERE entity_type='RULE' AND internal_id=%s",(self.s1,))
            conn.commit()

    def test_s1_active_episode_clears_with_frozen_version(self):
        t0=datetime(2026,11,5,1,0)
        with db_connection() as conn:
            with conn.cursor() as cur:
                self.assertEqual(on_telemetry(cur,device_id=self.th,metric="temperature",value=31,
                    measured_at=t0,received_at=t0),1)
                apply_rule(cur,self.s1_data(5,40),sync_mapping=True)
                self.assertEqual(on_telemetry(cur,device_id=self.th,metric="temperature",value=29,
                    measured_at=t0+timedelta(seconds=1),received_at=t0+timedelta(seconds=1)),1)
                cur.execute("SELECT occurrence_kind,payload_json->>'rule_version' FROM alarm_occurrences WHERE device_id=%s ORDER BY id",(self.th,))
                self.assertEqual(cur.fetchall(),[("TRIGGERED","4"),("CLEARED","4")])
                self.assert_frozen_delivery_pair(cur,self.th,4)
            conn.commit()

    def test_s6_pending_episode_keeps_300_seconds_after_upgrade(self):
        t0=datetime(2026,11,5,2,0)
        with db_connection() as conn:
            with conn.cursor() as cur:
                on_event(cur,device_id=self.dr,event_id=f"open-{self.suffix}",event_type="DOOR_OPENED",
                         observed_at=t0,received_at=t0,payload={})
                apply_rule(cur,self.s6_data(4,10),sync_mapping=True)
                self.assertEqual(evaluate_due_rules(cur,t0+timedelta(seconds=200),self.dr),0)
                self.assertEqual(evaluate_due_rules(cur,t0+timedelta(seconds=300),self.dr),1)
                self.assertEqual(on_event(cur,device_id=self.dr,event_id=f"close-{self.suffix}",
                    event_type="DOOR_CLOSED",observed_at=t0+timedelta(seconds=301),
                    received_at=t0+timedelta(seconds=301),payload={}),1)
                cur.execute("SELECT occurrence_kind,payload_json->>'rule_version' FROM alarm_occurrences WHERE device_id=%s ORDER BY id",(self.dr,))
                self.assertEqual(cur.fetchall(),[("TRIGGERED","3"),("CLEARED","3")])
                self.assert_frozen_delivery_pair(cur,self.dr,3)
            conn.commit()

    def test_disable_does_not_cancel_existing_episode(self):
        t0=datetime(2026,11,5,3,0)
        with db_connection() as conn:
            with conn.cursor() as cur:
                on_event(cur,device_id=self.dr,event_id=f"open-disable-{self.suffix}",event_type="DOOR_OPENED",
                         observed_at=t0,received_at=t0,payload={})
                disabled=self.s6_data(4,10); disabled["enabled"]=False
                apply_rule(cur,disabled,sync_mapping=True)
                self.assertEqual(evaluate_due_rules(cur,t0+timedelta(seconds=299),self.dr),0)
                self.assertEqual(evaluate_due_rules(cur,t0+timedelta(seconds=300),self.dr),1)
            conn.commit()


if __name__ == "__main__":
    unittest.main()

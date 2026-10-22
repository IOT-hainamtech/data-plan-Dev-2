"""Ordered, all-or-nothing reconciliation for the proposed Bridge snapshot."""
import json
import ssl
import urllib.request

from core.database import db_connection
from api.integration import _internal
from api.models import DeviceIn, OrganizationIn, ProfileIn, RuleIn, SiteIn, ZoneIn
from registry.sync import (apply_device, apply_organization, apply_profile,
                           apply_rule, apply_site, apply_zone)

APPLIERS={"organization":apply_organization,"site":apply_site,"zone":apply_zone,
          "profile":apply_profile,"device":apply_device,"rule":apply_rule}
MODELS={"organization":OrganizationIn,"site":SiteIn,"zone":ZoneIn,
        "profile":ProfileIn,"device":DeviceIn,"rule":RuleIn}
ORDER={name:i for i,name in enumerate(APPLIERS)}

class SnapshotClient:
    def __init__(self,url,token,timeout=10,allow_insecure_mock=False):
        if not url.startswith("https://") and not allow_insecure_mock: raise ValueError("Snapshot URL must use HTTPS")
        self.url,self.token,self.timeout=url,token,timeout
    def fetch(self):
        req=urllib.request.Request(self.url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/json"})
        context=ssl.create_default_context() if self.url.startswith("https://") else None
        with urllib.request.urlopen(req,timeout=self.timeout,context=context) as response:
            return json.loads(response.read().decode())

def reconcile_snapshot(snapshot,allowed_orgs):
    if snapshot.get("contract_version")!="1.3-proposed": raise ValueError("unsupported snapshot contract_version")
    resources=snapshot.get("resources")
    if not isinstance(resources,list): raise ValueError("snapshot resources must be a list")
    normalized=[]
    for item in resources:
        kind=item.get("kind"); payload=item.get("payload")
        if kind not in APPLIERS or not isinstance(payload,dict): raise ValueError("invalid snapshot resource")
        version_field="config_version" if kind=="device" else "version"
        wrapper_version=item.get("version")
        if version_field in payload and payload[version_field] != wrapper_version:
            raise ValueError(f"snapshot {kind} wrapper/payload version mismatch")
        public={**payload,version_field:wrapper_version}
        model=MODELS[kind].model_validate(public)
        org=public.get("org_code")
        if org and "*" not in allowed_orgs and org not in allowed_orgs: raise PermissionError(f"snapshot org not allowed: {org}")
        normalized.append((ORDER[kind],kind,_internal(model)))
    results=[]
    with db_connection() as conn:
        try:
            with conn.cursor() as cur:
                for _,kind,data in sorted(normalized,key=lambda x:x[0]): results.append(APPLIERS[kind](cur,data))
            conn.commit()
        except Exception:
            conn.rollback(); raise
    return results

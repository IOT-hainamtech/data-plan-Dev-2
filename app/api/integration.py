"""Dev1 -> Dev2 configuration and alarm lifecycle endpoints (v1.3 proposed)."""
from fastapi import APIRouter, Depends, HTTPException
from api.models import (AlarmStateIn, DeviceIn, OrganizationIn, ProfileIn,
                        RuleIn, SiteIn, ZoneIn)
from core.auth import authorize_org, service_context
from core.database import db_connection
from integrations.dev1_config import resolve_internal_alarm_key
from registry.sync import (apply_device, apply_organization, apply_profile,
                           apply_rule, apply_site, apply_zone, payload_hash)

router=APIRouter(prefix="/api/v1")

def _internal(model):
    """Map public v1.3 names to the stable internal registry vocabulary."""
    data=model.model_dump(mode="json")
    if isinstance(model,OrganizationIn):
        return {**data,"display_name":data.pop("name"),"metadata":{}}
    if isinstance(model,SiteIn):
        return {**data,"display_name":data.pop("name"),"enabled":True,"metadata":{}}
    if isinstance(model,ZoneIn):
        return {**data,"display_name":data.pop("name"),"enabled":True,"metadata":{}}
    if isinstance(model,ProfileIn):
        points=[]
        for point in data.pop("data_points"):
            points.append({**point,"min_value":point.pop("min"),"max_value":point.pop("max"),
                           "metadata":{"label":point.pop("label")}})
        events=[{"event_type":event["code"],"state_point":None,"state_value":None,
                 "label":event["label"]} for event in data.pop("event_types")]
        metadata={key:data.pop(key) for key in ("signal_model","protocol","expected_interval_s",
                                                "heartbeat_interval_s","offline_after_missed")}
        return {**data,"display_name":data["profile_code"],"enabled":True,"metadata":metadata,
                "data_points":points,"event_types":events}
    if isinstance(model,DeviceIn):
        return {**data,"version":data.pop("config_version"),
                "connectivity":data.pop("connectivity_type"),
                "telemetry_interval_seconds":60,"heartbeat_interval_seconds":60,
                "metadata":{"status":data.pop("status")}}
    if isinstance(model,RuleIn):
        scope=data.pop("scope"); ref=scope.pop("ref")
        scope[{"DEVICE":"device_id","PROFILE":"profile_code","ZONE":"zone_code",
               "SITE":"site_code","ORGANIZATION":"org_code"}[scope["type"]]]=ref
        schedule=data.pop("schedule",None) or {}
        return {**data,"rule_kind":data.pop("rule_type"),"metric":data.pop("data_point",None),
                "enabled":data.pop("active"),"scope":scope,
                "weekdays":schedule.get("weekdays",[]),"timezone":schedule.get("timezone"),
                "business_start":schedule.get("business_start"),"business_end":schedule.get("business_end")}
    return data

def _apply(model, context_org, expected, fn):
    public=model.model_dump(mode="json")
    for field,value in expected.items():
        if public.get(field)!=value: raise HTTPException(422,detail=f"URL {field} does not match payload")
    authorize_org(context_org,public.get("org_code",context_org))
    data=_internal(model)
    with db_connection() as conn:
        with conn.cursor() as cur: result=fn(cur,data)
        conn.commit()
    return result

@router.put("/sync/organizations/{org_code}")
def sync_organization(org_code:str, body:OrganizationIn, org:str=Depends(service_context)):
    return _apply(body,org,{"org_code":org_code},apply_organization)
@router.put("/sync/sites/{org_code}/{site_code}")
def sync_site(org_code:str,site_code:str,body:SiteIn,org:str=Depends(service_context)):
    return _apply(body,org,{"org_code":org_code,"site_code":site_code},apply_site)
@router.put("/sync/zones/{org_code}/{site_code}/{zone_code}")
def sync_zone(org_code:str,site_code:str,zone_code:str,body:ZoneIn,org:str=Depends(service_context)):
    return _apply(body,org,{"org_code":org_code,"site_code":site_code,"zone_code":zone_code},apply_zone)
@router.put("/sync/profiles/{profile_code}")
def sync_profile(profile_code:str,body:ProfileIn,org:str=Depends(service_context)):
    return _apply(body,org,{"profile_code":profile_code},apply_profile)
@router.put("/sync/devices/{device_id}")
def sync_device(device_id:str,body:DeviceIn,org:str=Depends(service_context)):
    return _apply(body,org,{"device_id":device_id},apply_device)
@router.put("/sync/rules/{rule_id}")
def sync_rule(rule_id:str,body:RuleIn,org:str=Depends(service_context)):
    return _apply(body,org,{"rule_id":rule_id},apply_rule)

@router.post("/alarms/{alarm_key}/state")
def alarm_state(alarm_key:str,body:AlarmStateIn,org:str=Depends(service_context)):
    if body.alarm_key != alarm_key: raise HTTPException(422,detail="URL alarm_key does not match payload")
    authorize_org(org,body.org_code); ranks={"acknowledged":1,"in_progress":2,"closed":3}; digest=payload_hash(body.model_dump(mode="json"))
    with db_connection() as conn:
        with conn.cursor() as cur:
            try:
                internal_alarm_key=resolve_internal_alarm_key(cur,alarm_key)
            except LookupError:
                raise HTTPException(404,"Alarm not found")
            except ValueError:
                raise HTTPException(409,"Alarm key mapping is ambiguous")
            cur.execute("""SELECT d.org_code FROM alarm_episode_mappings m JOIN devices d ON d.device_id=m.device_id
                WHERE m.wire_alarm_key=%s""",(internal_alarm_key,)); row=cur.fetchone()
            if not row: raise HTTPException(404,"Alarm not found")
            authorize_org(org,row[0]); cur.execute("SELECT state_rank,version,payload_hash FROM alarm_lifecycle WHERE wire_alarm_key=%s FOR UPDATE",(internal_alarm_key,)); old=cur.fetchone()
            if old:
                if body.version<old[1]: raise HTTPException(409,{"code":"STALE_VERSION","applied_version":old[1]})
                if body.version==old[1]:
                    if digest==old[2]: return {"status":"duplicate","resource_id":alarm_key,"applied_version":body.version,"duplicate":True}
                    raise HTTPException(409,{"code":"VERSION_PAYLOAD_CONFLICT","applied_version":old[1]})
                if ranks[body.state]<old[0]: raise HTTPException(409,{"code":"STATE_REGRESSION"})
            cur.execute("""INSERT INTO alarm_lifecycle(wire_alarm_key,org_code,state,state_rank,version,payload_hash,actor_ref,note,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(wire_alarm_key) DO UPDATE SET state=EXCLUDED.state,
              state_rank=EXCLUDED.state_rank,version=EXCLUDED.version,payload_hash=EXCLUDED.payload_hash,
              actor_ref=EXCLUDED.actor_ref,note=EXCLUDED.note,updated_at=EXCLUDED.updated_at""",
              (internal_alarm_key,body.org_code,body.state,ranks[body.state],body.version,digest,body.changed_by,None,body.changed_at))
        conn.commit()
    return {"status":"applied","resource_id":alarm_key,"applied_version":body.version,"duplicate":False}

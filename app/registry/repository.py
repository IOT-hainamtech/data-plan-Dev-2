"""Registry queries used by ingestion."""


def load_device_profile(cur, device_id, org_code, site_code):
    cur.execute("""SELECT profile_code FROM devices
        WHERE device_id=%s AND org_code=%s AND site_code=%s AND enabled=TRUE""",
        (device_id, org_code, site_code))
    row = cur.fetchone()
    return row[0] if row else None


def load_profile_contract(cur, profile_code, channel):
    cur.execute("""SELECT point_code,data_type,min_value,max_value,allowed_values,required
        FROM device_profile_points WHERE profile_code=%s AND channel=%s""", (profile_code, channel))
    return {code: {"data_type": data_type, "min_value": minimum, "max_value": maximum,
                   "allowed_values": allowed, "required": required}
            for code, data_type, minimum, maximum, allowed, required in cur.fetchall()}


def load_profile_event_types(cur, profile_code, state_point):
    cur.execute("""SELECT state_value,event_type FROM device_profile_events
        WHERE profile_code=%s AND state_point=%s""", (profile_code, state_point))
    return dict(cur.fetchall())

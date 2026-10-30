-- URD v1.1: S6 heartbeat is telemetry; retain legacy status point for old firmware.
INSERT INTO device_profile_points
    (profile_code,channel,point_code,data_type,unit,min_value,max_value,allowed_values,required)
VALUES ('DOOR_SENSOR','telemetry','door_state','string',NULL,NULL,NULL,
        '["open","closed"]'::jsonb,TRUE)
ON CONFLICT (profile_code,channel,point_code) DO UPDATE
SET data_type=EXCLUDED.data_type,allowed_values=EXCLUDED.allowed_values,required=EXCLUDED.required;

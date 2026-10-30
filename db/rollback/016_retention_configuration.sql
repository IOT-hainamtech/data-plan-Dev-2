-- Roll back only the control objects. Disable the policy first so no future
-- chunks are removed; already-retained rows cannot be recreated by rollback.
SELECT disable_telemetry_retention('rollback-016');
DROP FUNCTION IF EXISTS disable_telemetry_retention(VARCHAR);
DROP FUNCTION IF EXISTS enable_telemetry_retention(INTEGER,VARCHAR);
DROP TABLE IF EXISTS telemetry_retention_config;

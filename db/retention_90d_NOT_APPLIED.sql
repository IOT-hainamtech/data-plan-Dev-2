-- OPTIONAL / NOT PART OF AUTOMATIC MIGRATIONS.
-- Review, back up, and obtain explicit data-deletion approval before applying.
-- TimescaleDB will delete telemetry chunks older than 90 days after this policy
-- is enabled. Merely keeping this file in source does not alter runtime data.
SELECT add_retention_policy('telemetry', INTERVAL '90 days', if_not_exists => TRUE);

-- Correct telemetry quality to the URD 6.4/6.5 vocabulary without data loss.
ALTER TABLE telemetry_reports
    DROP CONSTRAINT IF EXISTS telemetry_reports_quality_check;

UPDATE telemetry_reports SET quality = 'good' WHERE quality = 'ok';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_telemetry_reports_quality'
          AND conrelid = 'telemetry_reports'::regclass
    ) THEN
        ALTER TABLE telemetry_reports ADD CONSTRAINT ck_telemetry_reports_quality
            CHECK (quality IN ('good', 'error'));
    END IF;
END $$;

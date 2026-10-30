-- Additive counters for URD U-ING-05. No telemetry/history is removed.
CREATE TABLE IF NOT EXISTS ingestion_metrics (
    org_code VARCHAR(64) NOT NULL,
    metric_name VARCHAR(48) NOT NULL,
    error_code VARCHAR(48) NOT NULL DEFAULT '',
    metric_value BIGINT NOT NULL DEFAULT 0,
    latency_sum_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    PRIMARY KEY (org_code, metric_name, error_code)
);

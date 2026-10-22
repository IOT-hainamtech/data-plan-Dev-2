-- Development Bridge persistence used to verify durable-ACK and snapshot recovery semantics.
CREATE TABLE IF NOT EXISTS bridge_event_inbox (
    event_id VARCHAR(160) PRIMARY KEY,
    event_kind VARCHAR(16) NOT NULL CHECK (event_kind IN ('alarm','status')),
    payload_hash VARCHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    ack_id UUID NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS bridge_snapshot_store (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    snapshot_json JSONB NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

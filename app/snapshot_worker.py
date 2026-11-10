import logging, os, time
from core.clock import now_utc
from core.database import db_connection
from integrations.dev1_config import Dev1ConfigClient, reconcile_device_config
from integrations.mapping import discover_confirmed_devices, load_and_apply_manifest
from integrations.snapshot import SnapshotClient, reconcile_snapshot
from status.service import enqueue_status_reconciliation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("snapshot-worker")


def run_reconciliation_cycle(config_reconcile, status_reconcile):
    """Run independent obligations so a Dev 1 config outage cannot suppress IF-04."""
    try:
        config_reconcile()
    except Exception:
        LOG.exception("Configuration reconciliation failed; last valid configuration retained")
    try:
        status_reconcile()
    except Exception:
        LOG.exception("Status reconciliation failed; queued state retained for retry")


def init_mappings():
    """Apply manifest mappings on startup if available."""
    manifest_file = os.getenv("INTEGRATION_MAPPINGS_FILE", "config/integration_mappings.json")
    if os.path.exists(manifest_file):
        try:
            with db_connection() as conn:
                res = load_and_apply_manifest(conn, file_path=manifest_file)
                if res.get("total", 0) > 0:
                    LOG.info("INTEGRATION_MANIFEST_INITIALIZED total=%d created=%d updated=%d",
                             res["total"], res["created"], res["updated"])
        except Exception:
            LOG.exception("Failed to initialize integration mappings manifest")


def main():
    init_mappings()
    token = os.getenv("BRIDGE_SNAPSHOT_TOKEN") or os.environ.get("BRIDGE_OUTBOUND_TOKEN")
    timeout = float(os.getenv("BRIDGE_TIMEOUT_SECONDS", "10"))
    dev1_url = os.getenv("DEV1_CONFIG_URL")
    dev1_site = os.getenv("DEV1_SITE_NAME")

    actual_client = None
    if dev1_url and dev1_site:
        allow_insecure = os.getenv("DEV1_ALLOW_INSECURE_LAB", "false").lower() == "true"
        actual_client = Dev1ConfigClient(
            dev1_url, timeout, token, dev1_site, allow_insecure_lab=allow_insecure
        )

    client = None
    if not actual_client and os.getenv("BRIDGE_SNAPSHOT_URL"):
        client = SnapshotClient(
            os.environ["BRIDGE_SNAPSHOT_URL"], token, timeout,
            os.getenv("BRIDGE_ALLOW_INSECURE_MOCK", "false").lower() == "true"
        )

    allowed = {x.strip() for x in os.getenv("BRIDGE_ALLOWED_ORGS", "").split(",") if x.strip()}
    interval = max(30, int(os.getenv("SNAPSHOT_INTERVAL_SECONDS", "300")))

    while True:
        def reconcile_configuration():
            if actual_client:
                configured_ids = [x.strip() for x in os.getenv("DEV1_CONFIG_DEVICE_IDS", "").split(",") if x.strip()]
                if not configured_ids:
                    with db_connection() as conn:
                        with conn.cursor() as cur:
                            configured_ids = discover_confirmed_devices(cur)
                if not configured_ids:
                    LOG.warning("No confirmed devices to reconcile with Dev 1")
                    return
                configs = [actual_client.get_device_config(device_id) for device_id in configured_ids]
                with db_connection() as conn:
                    try:
                        with conn.cursor() as cur:
                            results = [result for config in configs for result in reconcile_device_config(cur, config)]
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                LOG.info("DEV1_CONFIG_RECONCILED resources=%d", len(results))
            elif client:
                LOG.info("SNAPSHOT_RECONCILED resources=%d", len(reconcile_snapshot(client.fetch(), allowed)))

        def reconcile_status():
            with db_connection() as conn:
                with conn.cursor() as cur:
                    status_count = enqueue_status_reconciliation(cur, now_utc())
                conn.commit()
            LOG.info("STATUS_RECONCILED queued=%d", status_count)

        run_reconciliation_cycle(reconcile_configuration, reconcile_status)
        time.sleep(interval)


if __name__ == "__main__":
    main()

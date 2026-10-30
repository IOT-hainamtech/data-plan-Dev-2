import logging,os,time
from core.clock import now_utc
from core.database import db_connection
from integrations.dev1_config import Dev1ConfigClient,reconcile_device_config
from integrations.snapshot import SnapshotClient,reconcile_snapshot
from status.service import enqueue_status_reconciliation

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
LOG=logging.getLogger("snapshot-worker")

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

def main():
    token=os.getenv("BRIDGE_SNAPSHOT_TOKEN") or os.environ["BRIDGE_OUTBOUND_TOKEN"]
    actual_ids=[x.strip() for x in os.getenv("DEV1_CONFIG_DEVICE_IDS","").split(",") if x.strip()]
    actual_client = Dev1ConfigClient(os.environ["DEV1_CONFIG_URL"],
        float(os.getenv("BRIDGE_TIMEOUT_SECONDS","10")), token,
        os.environ["DEV1_SITE_NAME"],
        allow_insecure_lab=os.getenv("DEV1_ALLOW_INSECURE_LAB","false").lower()=="true") if actual_ids else None
    client=None if actual_ids else SnapshotClient(os.environ["BRIDGE_SNAPSHOT_URL"],token,
        float(os.getenv("BRIDGE_TIMEOUT_SECONDS","10")),os.getenv("BRIDGE_ALLOW_INSECURE_MOCK","false").lower()=="true")
    allowed={x.strip() for x in os.getenv("BRIDGE_ALLOWED_ORGS","").split(",") if x.strip()}
    interval=max(30,int(os.getenv("SNAPSHOT_INTERVAL_SECONDS","300")))
    while True:
        def reconcile_configuration():
            if actual_client:
                configs=[actual_client.get_device_config(device_id) for device_id in actual_ids]
                with db_connection() as conn:
                    try:
                        with conn.cursor() as cur:
                            results=[result for config in configs for result in reconcile_device_config(cur,config)]
                        conn.commit()
                    except Exception:
                        conn.rollback(); raise
                LOG.info("DEV1_CONFIG_RECONCILED resources=%d",len(results))
            else:
                LOG.info("SNAPSHOT_RECONCILED resources=%d",len(reconcile_snapshot(client.fetch(),allowed)))

        def reconcile_status():
            with db_connection() as conn:
                with conn.cursor() as cur:
                    status_count=enqueue_status_reconciliation(cur,now_utc())
                conn.commit()
            LOG.info("STATUS_RECONCILED queued=%d",status_count)

        run_reconciliation_cycle(reconcile_configuration, reconcile_status)
        time.sleep(interval)
if __name__=="__main__": main()

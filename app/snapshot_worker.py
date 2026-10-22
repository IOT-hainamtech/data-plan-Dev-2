import logging,os,time
from integrations.snapshot import SnapshotClient,reconcile_snapshot

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
LOG=logging.getLogger("snapshot-worker")
def main():
    client=SnapshotClient(os.environ["BRIDGE_SNAPSHOT_URL"],os.environ["BRIDGE_OUTBOUND_TOKEN"],
        float(os.getenv("BRIDGE_TIMEOUT_SECONDS","10")),os.getenv("BRIDGE_ALLOW_INSECURE_MOCK","false").lower()=="true")
    allowed={x.strip() for x in os.getenv("BRIDGE_ALLOWED_ORGS","").split(",") if x.strip()}
    interval=max(30,int(os.getenv("SNAPSHOT_INTERVAL_SECONDS","300")))
    while True:
        try: LOG.info("SNAPSHOT_RECONCILED resources=%d",len(reconcile_snapshot(client.fetch(),allowed)))
        except Exception: LOG.exception("Snapshot reconciliation failed; last valid configuration retained")
        time.sleep(interval)
if __name__=="__main__": main()

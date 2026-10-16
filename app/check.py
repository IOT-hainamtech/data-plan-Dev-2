"""Run with: docker compose exec -T data-plane python check.py"""
from ingestor import db_connection

QUERIES = {
    "Registered demo devices": "SELECT COUNT(*) FROM devices",
    "Telemetry points": "SELECT COUNT(*) FROM telemetry",
    "S6 events": "SELECT COUNT(*) FROM device_events",
    "Status transitions": "SELECT COUNT(*) FROM device_status_history",
    "Alarm occurrences": "SELECT COUNT(*) FROM alarm_occurrences",
    "Outbox pending (not sent)": "SELECT COUNT(*) FROM alarm_outbox WHERE delivery_state='PENDING'",
    "Outbox in flight": "SELECT COUNT(*) FROM alarm_outbox WHERE delivery_state='IN_FLIGHT'",
    "Outbox delivered/ACKed": "SELECT COUNT(*) FROM alarm_outbox WHERE delivery_state='DELIVERED'",
    "Outbox terminal failures": "SELECT COUNT(*) FROM alarm_outbox WHERE delivery_state='FAILED'",
    "Outbox delivery failures": "SELECT COALESCE(SUM(failure_count),0) FROM alarm_outbox",
}

def main():
    with db_connection() as conn:
        with conn.cursor() as cursor:
            print("--- nextWireless Dev 2 / PostgreSQL + TimescaleDB ---")
            for label, query in QUERIES.items():
                cursor.execute(query)
                print(f"{label}: {cursor.fetchone()[0]}")
            print("\nLast S1 samples (UTC):")
            cursor.execute("""SELECT device_id, metric, value_number, measured_at
                FROM telemetry ORDER BY id DESC LIMIT 8""")
            for row in cursor.fetchall():
                print(row)
            print("\nLast S6 events (UTC):")
            cursor.execute("""SELECT device_id, event_type, event_at
                FROM device_events ORDER BY id DESC LIMIT 5""")
            for row in cursor.fetchall():
                print(row)
            print("\nRecent alarm occurrences (local only, not delivered to nextGRP):")
            cursor.execute("""SELECT rule_code,device_id,occurrence_kind,event_type,triggered_at
                FROM alarm_occurrences ORDER BY id DESC LIMIT 8""")
            for row in cursor.fetchall():
                print(row)
            print("\nCurrent device status:")
            cursor.execute("SELECT device_id, current_state, received_at FROM device_status ORDER BY device_id")
            for row in cursor.fetchall():
                print(row)

if __name__ == "__main__":
    main()

# S1 Physical Temperature Alarm Acceptance

- Result: **PASS**
- Reason: Runtime contains one matching physical TRIGGERED/CLEARED episode and both Outbox rows.
- Device/Rule: **TH-001 / S1_TEMP_HIGH**
- Organization: **hainam**
- Started (Asia/Ho_Chi_Minh): 2026-10-31 16:40:34 +07:00
- Finished (Asia/Ho_Chi_Minh): 2026-10-31 17:04:41 +07:00
- Data API samples observed: 224
- Baseline occurrence ID: 948
- Safety: read-only Data API GET and PostgreSQL SELECT only; Outbox Worker was not started.

## Acceptance rule

PASS requires a new runtime TRIGGERED and CLEARED for TH-001/S1_TEMP_HIGH with
org_code=hainam, exactly one of each, the same internal episode_key, the same
wire_alarm_key, and corresponding Outbox rows. Local >30/<29 counters are only
operator guidance and are not Rule Engine evidence.

## PostgreSQL evidence

~~~json
{
    "device":  {
                   "device_id":  "TH-001",
                   "org_code":  "hainam",
                   "site_code":  "hq",
                   "zone_code":  "office",
                   "enabled":  true
               },
    "rules":  [
                  {
                      "rule_code":  "S1_DEMO_TEMP_HIGH",
                      "enabled":  false,
                      "config_version":  2,
                      "threshold_value":  30,
                      "hold_seconds":  0,
                      "hysteresis_value":  1,
                      "clear_hold_seconds":  120
                  },
                  {
                      "rule_code":  "S1_TEMP_HIGH",
                      "enabled":  true,
                      "config_version":  0,
                      "threshold_value":  30,
                      "hold_seconds":  300,
                      "hysteresis_value":  1,
                      "clear_hold_seconds":  120
                  }
              ],
    "runtime":  {
                    "rule_code":  "S1_TEMP_HIGH",
                    "device_id":  "TH-001",
                    "is_active":  false,
                    "hold_started_at":  null,
                    "recovery_started_at":  null,
                    "last_sample_at":  "2026-10-31 10:04:31",
                    "last_fired_at":  "2026-10-31 09:50:26",
                    "episode_key":  null,
                    "episode_started_at":  null
                },
    "max_occurrence_id":  950,
    "occurrences":  [
                        {
                            "id":  949,
                            "occurrence_kind":  "TRIGGERED",
                            "episode_key":  "ep:S1_TEMP_HIGH:3a660641a07ec49edb8d61bdc0112121",
                            "wire_alarm_key":  "S1_TEMP_HIGH:TH-001:1793439926",
                            "triggered_at":  "2026-10-31 09:50:27.474639",
                            "cleared_at":  null,
                            "org_code":  "hainam",
                            "occurrence_seq":  "1",
                            "delivery_state":  "PENDING",
                            "outbox_id":  949
                        },
                        {
                            "id":  950,
                            "occurrence_kind":  "CLEARED",
                            "episode_key":  "ep:S1_TEMP_HIGH:3a660641a07ec49edb8d61bdc0112121",
                            "wire_alarm_key":  "S1_TEMP_HIGH:TH-001:1793439926",
                            "triggered_at":  "2026-10-31 09:59:56.592504",
                            "cleared_at":  "2026-10-31 09:59:56.592504",
                            "org_code":  "hainam",
                            "occurrence_seq":  "2",
                            "delivery_state":  "PENDING",
                            "outbox_id":  950
                        }
                    ],
    "outbox_counts":  {
                          "DELIVERED":  206,
                          "PENDING":  27
                      }
}
~~~

## Limitations

The monitor cannot prove how the sensor was heated. Physical handling, safe heat
application, device identity and absence of simulated MQTT publishers remain
operator-observed controls.
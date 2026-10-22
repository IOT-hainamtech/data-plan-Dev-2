# Dev 1 Pending Confirmations

The implementation follows `1.3-proposed`; it is not proof of production nextGRP integration.

- Approve the alarm event types, nested event body, severity casing and deterministic alarm/episode key.
- Approve ISO weekday numbering, Asia/Ho_Chi_Minh schedule semantics and the proposed S6 boundary behavior.
- Provide a reachable HTTPS `BRIDGE_BASE_URL` and a service token through a private secret channel.
- Confirm token rotation, per-organization authorization policy, rate limits and operational timeouts.
- Confirm whether device identifiers are globally unique. They currently are a database primary key; the proposed alarm-key format does not include `org_code`.
- Confirm whether profiles remain globally keyed or become organization-scoped.
- Confirm dashboard pagination/cursor requirements beyond the bounded M1 `limit` parameters.
- Confirm tombstone semantics. M1 omission never hard-deletes a resource; retire/disable requires a higher version.
- Run contract tests against the real Bridge/Frappe stack and approve durable ACK behavior before production use.

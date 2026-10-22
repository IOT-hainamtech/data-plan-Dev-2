# Data Plane Stabilization Report

Ngày rà soát: 2026-10-21 (Asia/Ho_Chi_Minh)
Branch: `feat/esp32-firmware`
HEAD ban đầu: `6c5d595 feat: complete ESP32 provisioning, LAN MQTT configuration, and test suites`

## 1. Trạng thái repository ban đầu

Repository không sạch và các thay đổi chưa commit đã được giữ nguyên, không reset/restore/clean:

- Modified: `app/api.py`, `app/ingestor.py`, `app/migrate.py`, `app/rule_engine.py`, `app/test_outbox_integration.py`, `app/test_rule_engine.py`, `app/test_rule_engine_integration.py`, `compose.yaml`, `mosquitto/acl`, `mosquitto/start-secure.sh`, `scripts/generate-dev-mqtt-assets.sh`.
- Untracked ban đầu: `app/007_status_tracker_timeout.sql`, `app/test_e2e_simulator.py`, `app/test_multi_device_integration.py`, `app/test_status_tracker.py`, `db/007_status_tracker_timeout.sql`.
- Hai bản migration `app/007_status_tracker_timeout.sql` và `db/007_status_tracker_timeout.sql` có SHA-256 giống nhau. Đây là cách bố trí hiện hữu: bản trong `db/` dùng khi tạo volume mới, bản trong `app/` dùng bởi `migrate.py` cho database đã tồn tại.
- Docker daemon không hoạt động/không có named pipe `docker_engine`; không có container nào được thay đổi.
- PostgreSQL trả lời TCP ở `127.0.0.1:5433`, nhưng credential `nw_user`/`PG_PASSWORD` của workspace bị từ chối. Không áp migration lên database không xác định này.
- PostgreSQL không trả lời ở `127.0.0.1:5432`.
- MQTT LAN cũ `192.168.1.87:8883` không reachable tại thời điểm kiểm tra. Không thay firewall, router, CA hay credential.

## 2. Phạm vi code và luồng dữ liệu đã rà soát

Luồng hiện tại:

1. Firmware hoặc simulator publish QoS 1 tới `nw/v1/{org}/{site}/{device_id}/{channel}` qua MQTT TLS.
2. `ingestor.validate_message()` kiểm tra topic, schema version, `device_id`, `boot_id`, `seq`, timestamp và channel.
3. `save_message()` xác nhận device/profile trong `devices`, ghi báo cáo vào `telemetry_reports`, số đo vào hypertable `telemetry`, event vào `device_events`, và presence vào `device_status`/`device_status_history`.
4. `rule_engine.on_telemetry()` xử lý S1; `on_event()` và `evaluate_due_rules()` xử lý S6. Runtime bền nằm trong `rule_runtime_state`.
5. Trigger/clear được ghi vào `alarm_occurrences` và `alarm_outbox` trong cùng transaction.
6. `outbox_worker.OutboxWorker` claim bằng lease/`SKIP LOCKED`, gửi tới mock nextGRP và chỉ đánh dấu `DELIVERED` khi ACK hợp lệ.
7. `api.py` đọc telemetry, device/status/history/event/alarm/outbox metrics.

## 3. Các thay đổi Antigravity đang làm dở và đánh giá

### Status Tracker timeout

- Migration 007 bổ sung `last_seen_at`, `timeout_seconds`, partial index cho các device online, backfill từ `received_at`, đăng ký TH-002 và rule S1 tương ứng.
- `update_device_presence()` chuyển telemetry/event hợp lệ thành online, giữ `last_seen_at` khi nhận offline, và chỉ ghi history khi state thực sự đổi.
- `reconcile_device_timeouts()` khóa các dòng online quá hạn, ghi đúng một transition offline và được gọi từ rule timer.
- `save_message()` xử lý reconnect về online khi telemetry/event mới hợp lệ đến.
- Test mới bao phủ birth, LWT, refresh last-seen, reconnect, timeout và chống transition trùng.

### S1 continuity

- `on_telemetry()` bỏ sample cũ (`measured_at <= last_sample_at`).
- Gap lớn hơn 120 giây reset cửa sổ hold/recovery, tránh xem dữ liệu gián đoạn là “liên tục”.
- Test integration đã được đổi sang chuỗi sample 60 giây để phản ánh yêu cầu 300 giây liên tục và 120 giây clear.

### Multi-device và MQTT ACL

- TH-002 có rule/runtime độc lập, ACL riêng và credential development riêng.
- Đây là kiểm tra cô lập nhiều device trong cùng org/site, không phải triển khai kiến trúc multi-organization/site/zone.

### API

- Bổ sung danh sách device, status detail/history, events và alarms. Các endpoint chỉ đọc và dùng query parameter hóa.

## 4. Lỗi/vấn đề đã tìm thấy và nguyên nhân

1. **Không thể chạy integration stack:** Docker daemon hiện không chạy. Nguyên nhân là named pipe Docker không tồn tại, không phải Compose health failure.
2. **Không thể dùng PostgreSQL đang nghe ở 5433:** credential của workspace không khớp. Không tự thay credential hoặc áp migration vì chưa chứng minh đây là database của dự án.
3. **MQTT LAN không reachable:** TCP tới `192.168.1.87:8883` thất bại. Không kết luận broker/config hỏng vì host có thể đã đổi IP hoặc đang tắt.
4. **Python hệ thống thiếu dependency:** baseline đầu tiên lỗi import `psycopg`. Đã dùng virtualenv kiểm thử cô lập theo đúng `app/requirements.txt`; đây không phải lỗi dependency declaration của source.
5. **PlatformIO ban đầu bị sandbox chặn cache global:** build chạy thành công sau khi cho phép PlatformIO dùng toolchain/cache hiện có.
6. **Whitespace:** `git diff --check` báo một blank line cuối `app/api.py`; không ảnh hưởng runtime.

Không phát hiện lỗi nghiêm trọng mới trong phần Python thuần đã thực thi. Không sửa nghiệp vụ S1/S6 chỉ để làm test pass.

## 5. File/hàm đã sửa trong working tree hiện tại

Các file dưới đây đã tồn tại dưới dạng thay đổi chưa commit trước lượt rà soát; lượt này bảo toàn và kiểm chứng chúng:

- `app/ingestor.py`: `update_device_presence()`, `reconcile_device_timeouts()`, tích hợp presence trong `save_message()`, timeout trong `rule_timer()`.
- `app/rule_engine.py`: reset continuity khi gap telemetry vượt 120 giây.
- `app/migrate.py`: chạy thêm migration 007.
- `app/api.py`: endpoint device/status/event/alarm.
- `db/007_status_tracker_timeout.sql` và bản runtime trong `app/`: schema timeout và dữ liệu TH-002.
- Các test status, E2E, multi-device, rule/outbox integration.
- `compose.yaml`: mount migration 007 cho database mới.
- `mosquitto/acl`, `mosquitto/start-secure.sh`, `scripts/generate-dev-mqtt-assets.sh`: account/ACL development cho TH-002.

Các thay đổi bổ sung trong lượt khôi phục integration được ghi tại mục 11–16 bên dưới. Không commit/push/stage tự động.

## 6. Kết quả kiểm thử

| Nhóm | Kết quả | Ghi chú |
| --- | --- | --- |
| Python syntax | PASS | `python -m compileall -q app` |
| Python unit/contract discovery | PASS | 53 discovered; 20 executed PASS, 33 integration tests skipped đúng theo env flags |
| Firmware payload/provisioning contract | PASS | 9/9 |
| Firmware SHT31 build | PASS | ESP-IDF/PlatformIO; RAM 10.6%, Flash 60.9% |
| Firmware MC-38/reed_door build | PASS | ESP-IDF/PlatformIO; RAM 10.6%, Flash 60.9% |
| Compose parse | PASS | `docker compose config --quiet`; chỉ có warning không đọc được Docker user config trong sandbox |
| DB migration 001–007 trên DB sạch | BLOCKED | Docker daemon không chạy; không có DB test riêng an toàn |
| DB integration: ingestion/rules/status/outbox | BLOCKED | Không có database dự án xác thực được |
| MQTT TLS/ACL/QoS | BLOCKED | Docker daemon không chạy; LAN broker cũ không reachable |
| E2E simulator → MQTT → DB → rules → outbox → mock | BLOCKED | Phụ thuộc Docker/MQTT/DB |
| FastAPI live health | BLOCKED | Service không chạy |
| Docker Compose health | BLOCKED | Docker daemon không chạy |
| Hardware ESP32/SHT31/MC-38 | PENDING HARDWARE | Không yêu cầu phần cứng ở lượt này |

## 7. Vấn đề chưa giải quyết và rủi ro

- Chưa chứng minh migration 007 chạy được từ database trống và chạy lặp lại trên database đã có dữ liệu. Phải kiểm thử trên database test riêng trước khi áp vào volume hiện tại.
- Chưa có bằng chứng runtime cho timeout/LWT/reconnect, outbox recovery, concurrent claim, MQTT ACL/QoS và full E2E do stack không chạy.
- `timeout_seconds` hiện mặc định 120 giây cho mọi device. Nếu chu kỳ publish khác nhau, cần cấu hình theo device/profile đã được Dev 1 chốt; không tự mở rộng schema trong lượt ổn định này.
- Ngưỡng gap S1 120 giây đang là hằng số code tương ứng 2 × chu kỳ 60 giây giả định. Cần chốt sampling contract nếu firmware đổi chu kỳ.
- Mô hình hiện tại hardcode dữ liệu seed `demo/demo-site` và rule/device cụ thể trong migration. Chưa phải kiến trúc multi-organization/site/zone.
- nextGRP hiện chỉ là mock; không được suy diễn `DELIVERED` là hệ thống thật đã nhận.

## 8. Phần cần Dev 1 / nextGRP

- Xác nhận contract IF-01…IF-07, ACK/idempotency chính thức, authentication và endpoint production.
- Xác nhận sampling interval/timeout theo profile/device và chính sách clock skew.
- Xác nhận mô hình Organization/Site/Zone trước khi thay seed/schema hiện tại.
- Cung cấp Docker Desktop/daemon hoạt động hoặc một database test riêng có credential hợp lệ để chạy toàn bộ integration suite.
- Xác nhận IP hiện tại của MQTT LAN thay vì mặc định `192.168.1.87` vẫn đúng.

## 9. Phần chờ thiết bị thật

- Flash, NVS provisioning, Wi-Fi reconnect, TLS handshake và certificate validation trên ESP32.
- Đọc SHT31 thật, debounce/polarity MC-38, LWT khi mất nguồn/mất Wi-Fi, và soak test.

## 10. Việc đề xuất làm tiếp

1. Khởi động Docker Desktop, xác nhận `docker compose ps`, nhưng không chạy `down -v`.
2. Tạo database test tách biệt, chạy migration 001→007 hai lần để xác minh fresh build và idempotency.
3. Chạy `RUN_DB_TESTS=1`, sau đó MQTT security test với simulator tạm dừng theo README.
4. Chạy E2E và kiểm tra trực tiếp row qua từng bảng, không chỉ dựa vào trạng thái container.
5. Xác nhận IP MQTT LAN và chỉ test bằng credential/CA hiện có; không đổi firewall.
6. Sau khi tất cả integration test PASS, stage chọn lọc các file liên quan và review secrets trước commit.

Commit message đề xuất sau khi người dùng duyệt và integration test hoàn tất:

`fix: stabilize device status tracking and data-plane integration`

## 11. Cập nhật khôi phục Docker và integration (2026-10-21)

Mục này thay thế các trạng thái `BLOCKED` ở mục 6 sau khi người dùng bật Docker Desktop.

- Docker Desktop 4.80.0, Engine 29.6.1 hoạt động bằng context `desktop-linux`.
- Project Compose đúng là `nextwireless-dev2-pg`; bảy service đã được khôi phục: PostgreSQL, MQTT, Data Plane, Data API, Outbox Worker, Mock nextGRP và Simulator.
- PostgreSQL thực tế: container `nextwireless-dev2-pg-postgres-1`, database `nextwireless`, user `nw_user`, cổng 5432 chỉ trong Docker network, volume `nextwireless-dev2-pg_postgres_data`.
- TimescaleDB extension 2.30.2; `telemetry` là hypertable. Không thay password và không xóa/recreate PostgreSQL volume.
- MQTT được publish an toàn tại `127.0.0.1:8883`. IPv4 Wi-Fi hiện tại là `192.168.1.110`, trong khi certificate SAN gồm `mqtt`, `localhost`, `127.0.0.1`, `192.168.1.87`. Vì IP LAN mới không có trong SAN và `.env` không đặt `MQTT_BIND_IP`, broker không được mở ra LAN. Không sửa firewall, CA hay tắt TLS.
- Broker restart test xác nhận `ingestor.on_disconnect()` ghi nhận disconnect và Paho tự reconnect; `on_connect()` subscribe lại topic wildcard.
- Simulator đã được khởi động lại sau MQTT/E2E tests. Cuối lượt, PostgreSQL, MQTT và Mock nextGRP healthy; tất cả service đều running.

## 12. Migration 001–007

Đã tạo container TimescaleDB test tạm bằng `tmpfs`, không publish port, không dùng volume của dự án:

- Fresh initialization 001→007: PASS.
- Đủ 10 bảng chính, TimescaleDB extension và hypertable `telemetry`: PASS.
- Các index `idx_telemetry_query`, `uq_alarm_episode_kind`, `idx_outbox_claim`, `idx_device_status_timeout`: PASS.
- Chạy lại 001→007 trên cùng DB test: PASS; không trùng device/rule.
- Migration 007 trên database dự án đã được `app/migrate.py` thực thi; `device_status` có `last_seen_at` và `timeout_seconds`, TH-002/rule tương ứng tồn tại.
- Container test tmpfs đã dừng và tự xóa. Database/volume demo không bị reset.

## 13. Lỗi phát hiện và sửa

### S1 trộn event-time và ingestion-time

`rule_engine.on_telemetry()` trước đây dùng `measured_at` để kiểm tra thứ tự/gap nhưng dùng `received_at` để tính hold/recovery/quiet. Một chuỗi mẫu hợp lệ cách nhau 60 giây nhưng được ingest nhanh không thể đạt hold 300 giây.

Đã sửa:

- Dùng `measured_at` nhất quán cho state machine S1: hold, recovery, quiet window, episode start và gap reset.
- Tiếp tục dùng `received_at` khi ghi occurrence/outbox, để `alarm_outbox.next_attempt_at` không bị đẩy vào tương lai bởi device timestamp.
- Giữ nguyên cơ chế bỏ sample cũ và transaction alarm/outbox.

File: `app/rule_engine.py`, hàm `on_telemetry()`.

### E2E S1 chưa kiểm tra alarm/outbox

Test cũ chỉ publish một mẫu 27.5°C dù tên test nói outbox delivery. Đã đổi sang TH-002 test path, publish sáu mẫu 31.5°C cách nhau 60 giây, yêu cầu đúng một alarm sau 300 giây và ACK hợp lệ từ Mock nextGRP.

### E2E S6 phụ thuộc quiet window demo

Test S6 có thể không tạo alarm nếu simulator vừa tạo alarm trong quiet window 600 giây. Test mới snapshot `rule_runtime_state`, tạm tạo state cô lập khi simulator dừng, xác minh OPEN ngoài giờ → alarm → ACK, cleanup theo đúng `alarm_key`, rồi khôi phục runtime snapshot.

File: `app/test_e2e_simulator.py`.

### Outbox recovery test bị mất

Đã bổ sung lại `test_expired_lease_is_reclaimed_after_restart()`: worker mới reclaim row `IN_FLIGHT` sau khi lease worker cũ hết hạn và delivery thành `DELIVERED` ở attempt 2.

File: `app/test_outbox_integration.py`.

## 14. Kết quả kiểm thử cuối

Tất cả 54 test ứng dụng duy nhất đã được chạy với các cờ integration phù hợp và PASS; không tính các dòng skip của discovery mặc định là thiếu kiểm thử vì từng test đó đã được chạy ở nhóm integration tương ứng.

| Nhóm | Kết quả |
| --- | --- |
| Python unit/contract | 20 PASS |
| DB integration tổng hợp | 28 PASS |
| Rule Engine regression sau sửa | 9 PASS (nằm trong bộ DB, chạy lại) |
| MQTT TLS/CA/auth/ACL/QoS/LWT | 6 PASS |
| MQTT multi-device ACL | 1 PASS |
| E2E S1/S6/LWT | 3 PASS |
| Mock nextGRP idempotent ACK | 1 PASS |
| Outbox integration/restart/concurrency/retry | 7 PASS (nằm trong bộ DB, chạy lại) |
| Migration fresh + idempotency | PASS |
| FastAPI health | PASS (`status=UP`) |
| Outbox live metrics | PASS (`pending=0`, `failed=0`) |
| Docker Compose runtime | PASS, 7/7 service running |
| Firmware contract | 9 PASS (lượt trước) |
| Firmware SHT31 / MC-38 build | PASS / PASS (không có firmware change trong lượt này) |

Tổng theo test case duy nhất: **63 PASS, 0 FAIL, 0 SKIPPED, 0 BLOCKED** gồm 54 application tests và 9 firmware contract tests. Kiểm thử phần cứng vẫn là `PENDING HARDWARE`, không tính là software test bị skip.

## 15. Giải thích kỹ thuật để báo cáo trưởng nhóm

### Ingestion

- `ingestor.on_message()` giải mã JSON và cô lập lỗi từng MQTT message để service không dừng.
- `validate_message()` kiểm tra cấu trúc topic, schema version, topic/device ID, `boot_id`, `seq`, timestamp và channel.
- `validate_telemetry_data()` kiểm tra `quality=good/error`, metric và quy tắc không tạo số đo giả khi sensor lỗi.
- `save_message()` xác nhận device/profile rồi ghi `telemetry_reports`, `telemetry`, `device_events`; `(device_id, boot_id, seq)` và `(device_id, event_id)` đảm bảo idempotency.

### Rule Engine S1/S6

- `rule_engine.on_telemetry()` đọc `rule_definitions`, khóa/khôi phục `rule_runtime_state`, gọi `threshold_transition()` cho S1 và xử lý hold 300 giây, hysteresis 1°C, clear 120 giây, quiet 600 giây và gap.
- `on_event()` xử lý OPEN/CLOSED và chống event cũ bằng `state_changed_at`.
- `evaluate_due_rules()` xử lý timer S6 không phụ thuộc message mới; `is_business_hours()` dùng `Asia/Ho_Chi_Minh` và `door_should_fire()` phân biệt ngoài giờ/ngưỡng 300 giây.

### Status Tracker

- `update_device_presence()` cập nhật `device_status`, `last_seen_at`, boot ID và chỉ ghi `device_status_history` khi state đổi.
- `reconcile_device_timeouts()` khóa các device online quá hạn, ghi một transition offline và được gọi trong `rule_timer()`.
- Telemetry/event hợp lệ sau reconnect tự chuyển device về online; status/LWT explicit chuyển offline.

### Alarm và Durable Outbox

- `_write_occurrence()` tạo idempotent `alarm_key`, ghi `alarm_occurrences` trước rồi ghi `alarm_outbox` trong cùng transaction của ingestion/rule timer.
- `OutboxStore.claim()` dùng committed lease và `FOR UPDATE SKIP LOCKED` để nhiều worker không claim cùng row.
- `OutboxWorker.run_once()` gửi qua `NextGrpAdapter`, chỉ gọi `delivered()` khi HTTP/ACK hợp lệ; lỗi HTTP, timeout hoặc ACK sai đi qua `failed()` với backoff/jitter.
- Lease hết hạn cho phép worker mới reclaim, ưu tiên replay an toàn hơn mất alarm. Mock nextGRP dùng `Idempotency-Key`/`alarm_key` và trả cùng ACK cho request trùng.

### Luồng bảng dữ liệu

`devices` → `telemetry_reports`/`telemetry` hoặc `device_events` → `device_status` + `device_status_history` → `rule_definitions` + `rule_runtime_state` → `alarm_occurrences` → `alarm_outbox` → Mock nextGRP.

## 16. Việc còn lại

- LAN MQTT chưa sẵn sàng cho ESP32 vì host đổi sang `192.168.1.110` nhưng certificate SAN vẫn là `192.168.1.87`; cần người vận hành xác nhận IP tĩnh/DHCP reservation và chủ động cấp lại development certificate nếu chọn IP mới. Không mở firewall trước khi chốt.
- Chưa flash/test ESP32/SHT31/MC-38 thật: NVS, Wi-Fi reconnect, TLS hostname/SAN, sensor reading, reed debounce/polarity, mất nguồn/LWT và soak test.
- nextGRP thật chưa được gọi; cần chốt contract/auth/endpoint IF-01…IF-07. `DELIVERED` hiện chỉ có nghĩa Mock nextGRP ACK.
- TimescaleDB container báo extension 2.30.2 trong khi image runtime nhận biết 2.31.0 mới hơn; không tự nâng cấp trong lượt ổn định này.

Commit message đề xuất:

`fix: stabilize event-time rules and complete data-plane integration tests`

# nextWireless M1 Dev 2 ↔ Dev 1 (nextGRP Frappe v15) Integration Handoff

> **Tài liệu bàn giao và kiểm soát tích hợp Phase-1 Data Plane (Dev 2) ↔ Management Plane (Dev 1 / nextGRP)**  
> **Thời điểm cập nhật:** 2026-10-30 (Hoàn tất Staging Isolation & Two-Way Test Suite)  
> **Trạng thái tổng thể:** Sẵn sàng kiểm thử tích hợp hai chiều trên Staging (Port 8003/5434). 104/104 tests PASS (68 Unit + 27 Contract + 9 Live Staging + 9 Two-Way Integration). Chưa gửi bất kỳ POST nào sang Dev 1. Chi tiết xem thêm `docs/DEV1_DEV2_STAGING_TEST.md`.

---

## 1. Những gì Codex đã làm

Trước khi hết usage limit, Codex đã:
1. Phân tích văn bản hợp đồng Dev 1 (`DEV1_nextGRP_contract_20261029.md`).
2. Khởi tạo 11 file khung bao gồm client, adapter, fixtures và runner:
   - `app/integrations/dev1_config.py` (Client đọc config và reconcile DB).
   - `app/integrations/frappe_client.py` (Client kết nối Frappe).
   - `app/integrations/nextgrp.py` (FrappeAlarmAdapter cho Outbox Worker).
   - `tests/e2e/dev1_dev2/__init__.py`.
   - `tests/e2e/dev1_dev2/contract.py` (Validation Phase-1 ban đầu).
   - `tests/e2e/dev1_dev2/runner.py` (CLI runner với 3 chế độ: offline, read-only, post).
   - `tests/e2e/dev1_dev2/README.md`.
   - `tests/e2e/dev1_dev2/fixtures/s1.json` (Fixture thiết bị nhiệt ẩm).
   - `tests/e2e/dev1_dev2/fixtures/s6.json` (Fixture thiết bị cảm biến cửa).
   - `tests/unit/test_frappe_adapter.py`.
   - `tests/unit/test_frappe_client.py`.
3. Giữ Status delivery ở trạng thái **fail-closed** (`status_worker = None` trong worker) vì Dev 1 chưa chốt schema ACK cho IF-04.
4. Thiết lập cơ chế double opt-in cho E2E staging POST (`DEV1_E2E_ALLOW_POST=NW-IT-ONLY` và `DEV1_E2E_APPROVED_DEVICES`).

*Hạn chế tồn đọng từ Codex:*
- **Lỗi xác thực nghiêm trọng:** Codex ghi nhầm và cho phép header `Authorization: token API_KEY` (thiếu `API_SECRET`), thậm chí viết unit test chấp nhận single key.
- Các phương thức `post_status`, `claim_command`, `ack_command` trong `frappe_client.py` chỉ raise `RuntimeError("not confirmed")` thay vì triển khai chuẩn hóa transport.
- Thiếu bộ kiểm thử offline hoàn chỉnh cho hợp đồng Dev 1 → Dev 2 (PUT sync và POST IF-06).
- Fixture `s6.json` thiếu định nghĩa schedule hợp lệ cho STATE_CHANGE rule.
- Runner ở chế độ read-only và post bị văng lỗi traceback unhandled thay vì trả báo cáo structured JSON khi thiếu môi trường.

---

## 2. Những gì Antigravity đã hoàn thành

1. **Chuẩn hóa Authentication (Ưu tiên cao nhất):**
   - Đính chính toàn bộ hệ thống sang đúng hợp đồng Frappe v15: `Authorization: token API_KEY:API_SECRET`.
   - Viết hàm chuẩn hóa `validate_and_normalize_credentials`:
     - Nhận cả dạng chuỗi ghép `KEY:SECRET` hoặc cặp tham số riêng `api_key`, `api_secret`.
     - Tuyệt đối fail-closed nếu thiếu một trong hai thành phần (bác bỏ hoàn toàn chuỗi đơn `api-key-only`).
     - Bảo mật: Không in credential, API key, hay secret vào exception message, `__repr__`, traceback hay log.
   - Đồng bộ chuẩn hóa này xuyên suốt `FrappeClient`, `Dev1ConfigClient`, `FrappeAlarmAdapter`, và `runner.py`.

2. **Hoàn thiện đầy đủ Frappe API Client (`FrappeClient`):**
   - **GET `config_controller.get_device_config`**: Query param `device_id`, parse `response.message`.
   - **POST `status_controller.receive_status`**: Form-urlencoded `device_id`, `state` (`online`/`offline`).
   - **POST `alarm_controller.receive_alarm`**: Form-urlencoded với trường `payload` là chuỗi JSON. Hỗ trợ retry giữ nguyên `event_id` và toàn bộ nội dung.
   - **POST `command_controller.claim_command`**: Form-urlencoded `device_id`, parse `response.message.command`.
   - **POST `command_controller.ack_command`**: Form-urlencoded `command_id`, `delivery_token`, `status` (`Succeeded`/`Failed`), `result_json`.
   - Xử lý lỗi HTTP: Phân loại chính xác retryable (408, 429, 500, 502, 503) và terminal error (401, 403, 404, 409, 422). Xử lý an toàn khi gateway trả về HTML (như 502 Bad Gateway) mà không gây unhandled crash.

3. **Mở rộng bộ kiểm thử E2E & Contract (`tests/e2e/dev1_dev2/`):**
   - Bổ sung `validate_auth_headers`, `validate_frappe_envelope`, `validate_alarm_ack`, `validate_command_claim_response`, `validate_command_ack_response`, `validate_dev1_to_dev2_sync`, `validate_if06_callback` vào `contract.py`.
   - Tạo mới `tests/e2e/dev1_dev2/test_contract.py` (11 tests offline) kiểm tra toàn diện các trường hợp hợp lệ, thiếu trường, xung đột schema, scope mismatch.
   - Tạo mới `tests/e2e/dev1_dev2/test_dev1_to_dev2.py` (16 tests offline) kiểm thử hợp đồng chiều Dev 1 → Dev 2: PUT config sync (Org, Site, Zone, Profile, Device, Rule), versioning (monotonic increase, duplicate payload idempotency, stale version 409, payload conflict 409), IF-06 lifecycle state regression (chặn quay lui closed → in_progress), và ánh xạ `resolve_internal_alarm_key`.

4. **Nâng cấp Runner (`runner.py`):**
   - Khi chạy `--mode offline`: Kiểm tra đầy đủ auth header, envelope, alarm ACK occurrence/duplicate, S1/S6 fixtures.
   - Khi chạy `--mode read-only`: Nếu chưa có credentials, trả báo cáo `SKIP` sạch (không raise unhandled exception, không báo PASS giả).
   - Khi chạy `--mode post`: Báo cáo `BLOCKED` kèm lý do rõ ràng nếu chưa có đủ double opt-in.

5. **Hoàn thiện Fixture S6:**
   - Bổ sung schedule (`business_start`, `business_end`, `schedule_timezone`: `Asia/Ho_Chi_Minh`) cho STATE_CHANGE rule của `NW-IT-DR001`.

6. **Bảo toàn 100% quy tắc an toàn:**
   - Không chạm vào database runtime.
   - Không bật Outbox Worker hay Simulator runtime.
   - Tuyệt đối chưa gửi bất kỳ HTTP POST nào tới Dev 1 thật.

---

## 3. Các file đã tạo / chỉnh sửa

| File | Hành động | Mục đích |
|---|---|---|
| `app/integrations/frappe_client.py` | Chỉnh sửa | Thêm `validate_and_normalize_credentials`, hoàn thiện full methods (config, status, alarm, claim, ack), xử lý HTML error |
| `app/integrations/nextgrp.py` | Chỉnh sửa | Chuẩn hóa `FrappeAlarmAdapter` theo `KEY:SECRET`, loại bỏ chấp nhận single key |
| `app/integrations/dev1_config.py` | Chỉnh sửa | Tích hợp chuẩn hóa credential, hỗ trợ zone fallback linh hoạt |
| `tests/e2e/dev1_dev2/contract.py` | Mở rộng | Bổ sung validators cho auth header, envelope, ACK, command claim/ack, dev1->dev2 sync, IF-06 callback |
| `tests/e2e/dev1_dev2/runner.py` | Nâng cấp | Thêm test auth/envelope trong offline mode, trả SKIP/BLOCKED an toàn cho read-only & post |
| `tests/e2e/dev1_dev2/fixtures/s6.json` | Chỉnh sửa | Bổ sung schedule đầy đủ cho S6 STATE_CHANGE rule |
| `tests/e2e/dev1_dev2/README.md` | Chỉnh sửa | Đính chính contract header `Authorization: token API_KEY:API_SECRET` |
| `tests/unit/test_frappe_client.py` | Viết lại/Mở rộng | 21 unit tests cho credential parsing, headers, GET config, POST status/alarm/claim/ack, error handling |
| `tests/unit/test_frappe_adapter.py` | Chỉnh sửa/Mở rộng | 8 unit tests, loại bỏ test single key, thêm test validate ACK và credential pair |
| `tests/e2e/dev1_dev2/test_contract.py` | Tạo mới | 11 offline contract tests cho fixtures, identity, ACK, error scenarios |
| `tests/e2e/dev1_dev2/test_dev1_to_dev2.py` | Tạo mới | 16 offline contract tests cho chiều Dev 1 → Dev 2, versioning, IF-06, key mapping |
| `docs/DEV1_DEV2_INTEGRATION_HANDOFF.md` | Tạo mới | Tài liệu checkpoint bàn giao tích hợp |

---

## 4. Danh sách endpoint và trạng thái

| STT | Luồng / Endpoint | Chiều | Request | Response / Auth | Trạng thái | Ghi chú |
|---|---|---|---|---|---|---|
| 1 | `config_controller.get_device_config` | Dev 2 → Dev 1 | GET `?device_id=...` | `{"message": ...}` / `token KEY:SECRET` | **READY** | Sẵn sàng chạy read-only khi Dev 1 cấp token và mở LAN port 8000 |
| 2 | `status_controller.receive_status` | Dev 2 → Dev 1 | POST form: `device_id`, `state` | `{"message": ...}` | **PARTIAL (Fail-Closed)** | Transport đã sẵn sàng; Dev 1 chưa chốt schema ACK (IF-04) nên không gửi tự động trong background |
| 3 | `alarm_controller.receive_alarm` | Dev 2 → Dev 1 | POST form: `payload=JSON_STRING` | `{"message": {"alarm_key":..., "duplicate":...}}` | **READY (Staging)** | Sẵn sàng cho staging POST khi được phê duyệt |
| 4 | `command_controller.claim_command` | Dev 2 → Dev 1 | POST form: `device_id` | `{"message": {"command": ...}}` | **BLOCKED (Demo scope)** | Endpoint transport sẵn sàng; tạm không triển khai runtime execution trong demo S1/S6 |
| 5 | `command_controller.ack_command` | Dev 2 → Dev 1 | POST form: `command_id`, `delivery_token`, `status`, `result_json` | `{"message": {"status": ...}}` | **BLOCKED (Demo scope)** | Endpoint transport sẵn sàng; chờ lệnh điều khiển thực tế |
| 6 | Dev 2 `PUT /api/v1/sync/...` (Config) | Dev 1 → Dev 2 | PUT JSON: Org, Site, Zone, Profile, Device, Rule | `{"status": "applied"/"duplicate"}` / Bearer token | **READY (Dev 2)** | Dev 2 đã có API v1.3; Dev 1 chưa phát triển tính năng push config |
| 7 | Dev 2 `POST /api/v1/alarms/{alarm_key}/state` | Dev 1 → Dev 2 | POST JSON: `state`, `version`, `changed_at`, `changed_by` | `{"status": "applied"/"duplicate"}` / Bearer token | **READY (Dev 2)** | Dev 2 đã hỗ trợ IF-06 callback; Dev 1 chưa hoàn thiện background worker gửi callback |
| 8 | Dev 2 Data Queries (`/latest`, `/telemetry`, `/events`, `/status`) | Dev 1 → Dev 2 | GET query params | JSON metrics, history | **READY** | Đã sẵn sàng phục vụ Dev 1 React Dashboard |

---

## 5. Kết quả kiểm thử thực tế

Tất cả các bộ kiểm thử đã được chạy và xác nhận 100% PASS:

### A. Unit Tests (`tests/unit/`)
```
Ran 68 tests in 0.162s — OK
- test_frappe_client: 21 PASS (auth pair, headers, config, status, alarm, claim, ack, HTTP retryable/terminal errors)
- test_frappe_adapter: 8 PASS (fail-closed auth, form payload, ACK validation, receipt)
- test_dev1_config: 9 PASS (client fetch, identity mappings, alarm mapping, schedule mapping)
- test_outbox_worker, test_rule_engine, test_snapshot_worker, test_status_reconciliation, security: 30 PASS
```

### B. E2E Contract Tests (`tests/e2e/dev1_dev2/`)
```
Ran 27 tests in 0.026s — OK
- test_contract: 11 PASS (S1/S6 fixtures, auth headers, envelope, alarm ACK, status, command claim/ack)
- test_dev1_to_dev2: 16 PASS (v1.3 translation, versioning, duplicate, stale version, payload conflict, IF-06 regression, key resolution)
```

### C. Live Staging & Two-Way Integration Tests (`tests/e2e/dev1_dev2/`)
```
Ran 18 tests in 1.202s — OK
- test_staging_live: 9 PASS (Health 200, negative auth 401/403, list devices NW-IT-TH001/DR001, telemetry/events, version lifecycle applied/duplicate/stale/conflict)
- test_two_way_integration: 9 PASS (8 scenarios: Dev2 GET config, Dev1 PUT sync, Dev2 POST status fail-closed, Dev2 POST alarm occurrence/duplicate/cleared, Dev1 POST IF-06 callback, Dev1 GET latest/telemetry/events, DB isolation & re-seed idempotency)
```

### D. Runner CLI
- `python -m tests.e2e.dev1_dev2.runner --mode offline`: **12 PASS, 2 PARTIAL, 2 BLOCKED** (0 failures).
- `python -m tests.e2e.dev1_dev2.runner --mode read-only`: **SKIP** (không có credential môi trường, đúng fail-closed).
- `python -m tests.e2e.dev1_dev2.runner --mode post`: **BLOCKED** (thiếu double opt-in, chặn an toàn).

---

## 6. Cấu hình mẫu an toàn cho Dev 2

Khi được cung cấp thông tin kết nối và token từ Dev 1, thiết lập biến môi trường trong session hiện tại (tuyệt đối không sửa `.env` gốc và không commit vào Git):

```powershell
# Địa chỉ Management Plane (thay đổi theo LAN IP thực tế của máy Dev 1)
$env:DEV1_BASE_URL = 'http://192.168.1.43:8000'
$env:DEV1_SITE_NAME = 'nextgrp.localhost'
$env:DEV1_ALLOW_HTTP_LAN = 'true'
$env:DEV1_TIMEOUT_SECONDS = '5'

# Cấu hình Token (Dùng 1 trong 2 cách, KHÔNG dùng token cũ từng lộ)
# Cách 1 (Combined):
$env:DEV1_SERVICE_TOKEN = '<API_KEY>:<API_SECRET>'

# Cách 2 (Cặp riêng biệt):
# $env:DEV1_API_KEY = '<API_KEY>'
# $env:DEV1_API_SECRET = '<API_SECRET>'
```

---

## 7. Cách chạy E2E Read-Only

Dùng để xác nhận Dev 1 đã đăng ký thiết bị test và trả về cấu hình JSON hợp lệ mà không làm thay đổi trạng thái hệ thống:

```powershell
# 1. Thiết lập biến môi trường kết nối (như Mục 6)
$env:PYTHONPATH = "app;."
$env:DEV1_BASE_URL = 'http://192.168.1.43:8000'
$env:DEV1_SITE_NAME = 'nextgrp.localhost'
$env:DEV1_ALLOW_HTTP_LAN = 'true'
$env:DEV1_SERVICE_TOKEN = '<API_KEY>:<API_SECRET>'

# 2. Chạy runner chế độ read-only
python -m tests.e2e.dev1_dev2.runner --mode read-only

# Kết quả mong đợi khi kết nối thành công:
# {
#   "mode": "read-only",
#   "results": [
#     {"subject": "NW-IT-TH001", "test": "GET config", "status": "PASS", "detail": ""},
#     {"subject": "NW-IT-DR001", "test": "GET config", "status": "PASS", "detail": ""}
#   ]
# }
```

---

## 8. Cách chạy E2E Staging POST khi được phê duyệt

> [!WARNING]
> Chỉ thực hiện khi Dev 1 đã xác nhận đã tạo và cho phép bắn dữ liệu thử nghiệm vào `NW-IT-TH001` và `NW-IT-DR001`. Tuyệt đối không dùng cho thiết bị vật lý `TH-001` / `DR-001`.

```powershell
# 1. Kích hoạt Double Opt-In tường minh
$env:DEV1_E2E_ALLOW_POST = 'NW-IT-ONLY'
$env:DEV1_E2E_APPROVED_DEVICES = 'NW-IT-TH001,NW-IT-DR001'

# 2. Chạy runner chế độ POST
python -m tests.e2e.dev1_dev2.runner --mode post

# 3. DỌN DẸP NGAY SAU KHI CHẠY (Bảo mật)
Remove-Item env:DEV1_SERVICE_TOKEN -ErrorAction SilentlyContinue
Remove-Item env:DEV1_API_KEY -ErrorAction SilentlyContinue
Remove-Item env:DEV1_API_SECRET -ErrorAction SilentlyContinue
Remove-Item env:DEV1_E2E_ALLOW_POST -ErrorAction SilentlyContinue
Remove-Item env:DEV1_E2E_APPROVED_DEVICES -ErrorAction SilentlyContinue
```

---

## 9. Những phần còn cần Dev 1 xác nhận

1. **IP và Port forwarding:** Xác nhận Windows LAN IP hiện tại của máy Dev 1 và portproxy cổng 8000 vào WSL2 đang hoạt động.
2. **Cặp Service Credential mới:** Cung cấp cặp API Key và API Secret dành riêng cho Data Plane (user `IoT Data Plane` thuộc Organization chỉ định).
3. **Đăng ký Device Staging:** Xác nhận đã tạo 2 thiết bị test trên nextGRP:
   - `NW-IT-TH001` (gắn profile S1 với metric temperature, humidity; rule threshold).
   - `NW-IT-DR001` (gắn profile S6 với metric door_state, event DOOR_OPENED/DOOR_CLOSED; rule state change có schedule).
   - *Lưu ý Profile S1:* Khắc phục việc `supports_telemetry = 0` và `data_points = []` trong cấu hình test cũ của Dev 1.
4. **IF-04 Status ACK schema:** Thống nhất định dạng phản hồi khi nhận status để Dev 2 mở lại background status sync.
5. **IF-06 Callback:** Thông báo khi Dev 1 hoàn tất background callback để kiểm thử tích hợp 2 chiều.

---

## 10. Trình tự kết nối hai máy (Dev 1 ↔ Dev 2)

```
[BƯỚC 1: KIỂM TRA MẠNG VẬT LÝ]
Dev 2 kiểm tra ping / telnet tới IP LAN của Dev 1:
Test-NetConnection -ComputerName <DEV1_LAN_IP> -Port 8000

        ↓ (Nếu kết nối TCP 8000 thành công)

[BƯỚC 2: TRAO ĐỔI CREDENTIAL AN TOÀN]
Dev 1 cấp cặp API Key/Secret qua kênh bảo mật nội bộ.
Dev 2 nạp vào session PowerShell:
$env:DEV1_SERVICE_TOKEN = "<API_KEY>:<API_SECRET>"

        ↓

[BƯỚC 3: E2E READ-ONLY (GET CONFIG)]
Dev 2 chạy:
python -m tests.e2e.dev1_dev2.runner --mode read-only
Kiểm tra cấu hình Device, Profile, Rule trả về từ nextGRP.

        ↓ (Nếu GET Config 100% PASS và Dev 1 xác nhận sẵn sàng nhận test data)

[BƯỚC 4: E2E STAGING POST (ALARM TEST)]
Dev 2 bật double opt-in và gửi thử nghiệm cảnh báo:
$env:DEV1_E2E_ALLOW_POST = 'NW-IT-ONLY'
$env:DEV1_E2E_APPROVED_DEVICES = 'NW-IT-TH001,NW-IT-DR001'
python -m tests.e2e.dev1_dev2.runner --mode post

        ↓

[BƯỚC 5: XÁC MINH TRÊN GIAO DIỆN NEXTGRP]
Dev 1 kiểm tra `IoT Event Inbox` và `IoT Alarm Episode`:
- Sự kiện occurrence được ghi nhận và hiển thị.
- Sự kiện retry duplicate ghi nhận `duplicate=True`, không tạo episode thừa.
- Sự kiện cleared cập nhật trạng thái kết thúc cảnh báo.
```

---

## 11. Hướng dẫn tiếp tục nếu agent bị ngắt

Nếu phiên làm việc bị ngắt quãng:
1. Đọc tài liệu này tại `docs/DEV1_DEV2_INTEGRATION_HANDOFF.md`.
2. Kiểm tra tính toàn vẹn của mã nguồn bằng lệnh:
   ```powershell
   git status
   git diff --check
   ```
3. Chạy lại toàn bộ unit và contract test suite để xác nhận môi trường:
   ```powershell
   docker compose run --rm --no-deps -e PYTHONPATH=/repo/app:/repo -v "${PWD}:/repo" -w /repo data-plane `
     python -m unittest tests/unit/test_frappe_client.py tests/unit/test_frappe_adapter.py tests/unit/test_dev1_config.py tests/e2e/dev1_dev2/test_contract.py tests/e2e/dev1_dev2/test_dev1_to_dev2.py
   ```
4. Kiểm tra runner offline:
   ```powershell
   $env:PYTHONPATH = "app;."
   python -m tests.e2e.dev1_dev2.runner --mode offline
   ```
5. Khi nhận được phản hồi từ Dev 1 (IP & Token), làm theo quy trình ở **Mục 7** và **Mục 10**.


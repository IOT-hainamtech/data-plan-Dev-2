# Hướng dẫn Kiểm thử Môi trường Staging Dev 2 ↔ Dev 1 nextGRP

Tài liệu này hướng dẫn chi tiết kiến trúc, quy trình khởi tạo, vận hành, kiểm thử tự động hai chiều và các biện pháp bảo đảm an toàn cho môi trường Staging Dev 2 (`nextwireless-dev2-staging`), kết nối với Dev 1 Management Plane (`nextGRP` trên nền Frappe v15).

---

## 1. Kiến trúc Staging Cô lập (Isolated Staging Architecture)

Môi trường Staging được thiết kế **hoàn toàn tách biệt** với môi trường Runtime / Demo hiện tại (`nextwireless-dev2-pg-*`). Hai môi trường không dùng chung database, volume, container, mạng hoặc cổng.

| Thuộc tính | Runtime / Demo Hiện Hữu | Staging Mới (nextGRP Integration) | Ghi chú cô lập |
| :--- | :--- | :--- | :--- |
| **Docker Compose Project** | `nextwireless-dev2-pg` | `nextwireless-dev2-staging` | Tên project độc lập |
| **Compose File** | `compose.yaml` | `compose.staging.yaml` | Cấu hình tách biệt |
| **Database Name** | `nextwireless` | `nextwireless_staging` | Không chung CSDL |
| **PostgreSQL User** | `nw_user` | `nw_staging_user` | Tài khoản độc lập |
| **PostgreSQL Host Port** | `5432` (hoặc internal) | `127.0.0.1:5434` | Chỉ bind localhost |
| **PostgreSQL Volume** | `postgres_data` | `postgres_staging_data` | Volume độc lập |
| **Data API Port** | `127.0.0.1:8002` | `127.0.0.1:8003` | Chỉ bind localhost |
| **Network** | `nextwireless-dev2-pg_default` | `nextwireless-dev2-staging_staging_net` | Bridge network riêng |
| **Organization Code** | `demo`, `v13-test` | `NW-LOCAL-ORG-001` | Multi-tenant tenant riêng |
| **Site Code** | `lab`, `demo-site` | `NW-LOCAL-SITE-001` | Phù hợp quy hoạch Dev 1 |
| **Thiết bị Test S1** | `TH-001` | `NW-IT-TH001` | Nhiệt độ/Độ ẩm (S1) |
| **Thiết bị Test S6** | `DR-001` | `NW-IT-DR001` | Cảm biến cửa Reed (S6) |

---

## 2. Quản lý Secret và Phân biệt Authentication

Hệ thống tích hợp hai chiều sử dụng hai cơ chế xác thực hoàn toàn khác nhau. **Tuyệt đối không dùng lẫn hai loại token này:**

### 2.1. Phía Dev 2 Data Plane (Dev 1 gọi sang Dev 2)
- **Cơ chế:** HTTP Header `Authorization: Bearer <STAGING_DP_INBOUND_TOKEN>` kèm `X-Org-Code: NW-LOCAL-ORG-001`.
- **Cấu hình:** Lưu trong `.secrets/staging.env`.
- **Bảo mật:** Secret được sinh bằng CSPRNG (Cryptographically Secure Pseudo-Random Number Generator). Không tái sử dụng token cũ từng xuất hiện trong log hoặc trao đổi chat.

### 2.2. Phía Dev 1 Management Plane / Frappe (Dev 2 gọi sang Dev 1)
- **Cơ chế:** HTTP Header `Authorization: token <API_KEY>:<API_SECRET>` kèm `X-Frappe-Site-Name: nextgrp.localhost`.
- **Hợp đồng Frappe:** Phải có cả hai thành phần `API_KEY` và `API_SECRET` nối nhau bằng dấu `:`. Client fail-closed ngay tại transport nếu thiếu một trong hai.
- **Bảo mật:** Không in API Secret vào stdout, log, traceback hoặc báo cáo kiểm thử.

### 2.3. Cách sinh secret staging mới (CSPRNG)
Nếu cần cấp lại credential cho staging, chạy script an toàn trên PowerShell:
```powershell
$pg_pass = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$dp_token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
@"
STAGING_PG_PASSWORD=$pg_pass
STAGING_DP_INBOUND_TOKEN=$dp_token
STAGING_DATA_API_PORT=8003
STAGING_PG_PORT=5434
STAGING_ALLOWED_ORGS=NW-LOCAL-ORG-001
"@ | Out-File -FilePath .secrets/staging.env -Encoding utf8
```

---

## 3. Lệnh Vận hành Staging An Toàn (CLI Runbook)

### 3.1. Khởi động môi trường Staging
Khởi động container PostgreSQL (TimescaleDB) và Data API staging:
```bash
docker compose -f compose.staging.yaml --env-file .secrets/staging.env up -d
```
*Lưu ý:* Container PostgreSQL sẽ tự động chạy toàn bộ migrations `001–016` từ thư mục `db/migrations` trong lần đầu khởi tạo volume.

### 3.2. Áp dụng Seed Dữ liệu Test (Idempotent Seed)
Lệnh seed được tách riêng khỏi lệnh khởi động container để tránh ghi đè dữ liệu khi container restart:
```bash
docker compose -f compose.staging.yaml --env-file .secrets/staging.env exec data-api python scripts/seed_staging.py
```
*Đặc tính chống ghi đè cấu hình:*
- Tất cả câu lệnh INSERT trong `db/staging_seed/099_staging_seed.sql` đều sử dụng `ON CONFLICT (...) DO NOTHING`.
- Khi chạy lại seed nhiều lần, `config_version` không bị reset, Profile/Rule do Dev 1 đồng bộ không bị ghi đè, và thiết bị đã bị vô hiệu hóa không bị bật lại.

### 3.3. Dừng môi trường Staging
Dừng dịch vụ mà vẫn giữ nguyên dữ liệu đã seed/sync:
```bash
docker compose -f compose.staging.yaml --env-file .secrets/staging.env down
```

### 3.4. Rollback và Làm sạch Staging (Không ảnh hưởng Runtime)
Khi cần xóa sạch môi trường staging để test lại từ đầu mà **không chạm vào runtime**:
```bash
docker compose -f compose.staging.yaml --env-file .secrets/staging.env down -v
```
Lệnh này chỉ xóa volume `nextwireless-dev2-staging_postgres_staging_data`, volume `postgres_data` của runtime vẫn an toàn tuyệt đối.

---

## 4. Kiểm tra và Smoke Test Endpoints

### 4.1. Health Check
```bash
curl -i http://127.0.0.1:8003/api/v1/health
```
**Kỳ vọng:** HTTP `200 OK`, JSON `{"status": "UP"}`.

### 4.2. Kiểm tra danh sách thiết bị có xác thực (Positive Auth)
```bash
# Đọc token từ .secrets/staging.env
curl -i -H "Authorization: Bearer <STAGING_DP_INBOUND_TOKEN>" \
     -H "X-Org-Code: NW-LOCAL-ORG-001" \
     http://127.0.0.1:8003/api/v1/devices
```
**Kỳ vọng:** HTTP `200 OK`, trả về mảng chứa `NW-IT-TH001` (S1) và `NW-IT-DR001` (S6).

### 4.3. Kiểm tra từ chối truy cập (Negative Auth)
1. **Thiếu Authorization header:**
   ```bash
   curl -i http://127.0.0.1:8003/api/v1/devices
   ```
   **Kỳ vọng:** HTTP `401 Unauthorized`.
2. **Sai Bearer token:**
   ```bash
   curl -i -H "Authorization: Bearer wrong-token" -H "X-Org-Code: NW-LOCAL-ORG-001" http://127.0.0.1:8003/api/v1/devices
   ```
   **Kỳ vọng:** HTTP `401 Unauthorized`.
3. **Sai X-Org-Code hoặc không được phân quyền:**
   ```bash
   curl -i -H "Authorization: Bearer <STAGING_DP_INBOUND_TOKEN>" -H "X-Org-Code: UNKNOWN-ORG" http://127.0.0.1:8003/api/v1/devices
   ```
   **Kỳ vọng:** HTTP `403 Forbidden`.

---

## 5. Quy trình Chạy Bộ Test Tự Động

### 5.1. Chạy Unit Tests
```bash
docker compose run --rm --no-deps -e PYTHONPATH=/repo/app:/repo -v "${PWD}:/repo" -w /repo data-plane python -m unittest discover -s tests/unit
```
*Kết quả:* **68/68 PASS (100%)**.

### 5.2. Chạy Offline E2E Contract Tests
```bash
docker compose run --rm --no-deps -e PYTHONPATH=/repo/app:/repo -v "${PWD}:/repo" -w /repo data-plane python -m unittest tests/e2e/dev1_dev2/test_contract.py tests/e2e/dev1_dev2/test_dev1_to_dev2.py
```
*Kết quả:* **27/27 PASS (100%)**.

### 5.3. Chạy Live Staging Tests & Hai Chiều Tích Hợp
```bash
python -m unittest tests/e2e/dev1_dev2/test_staging_live.py tests/e2e/dev1_dev2/test_two_way_integration.py
```
*Kết quả:* **18/18 PASS (100%)**.

### 5.4. Chạy Runner Phase-1
- Chế độ Offline:
  ```bash
  python tests/e2e/dev1_dev2/runner.py --mode offline
  ```
- Chế độ Read-Only (yêu cầu LAN và credentials Dev 1):
  ```bash
  python tests/e2e/dev1_dev2/runner.py --mode read-only
  ```
- Chế độ Staging POST (yêu cầu phê duyệt double opt-in):
  ```bash
  python tests/e2e/dev1_dev2/runner.py --mode post
  ```

---

## 6. Hướng Dẫn Mở Ngrok Cho Staging (Khi Được Phê Duyệt)

Hiện tại, URL ngrok cũ (`https://sequence-precision-bubbly.ngrok-free.dev`) trỏ vào cổng 8002 (Runtime). **Không được sử dụng tunnel này cho dữ liệu test staging.**

Khi sẵn sàng mở kết nối công khai để Dev 1 gọi vào Staging Dev 2:
1. Đảm bảo cổng 8003 đang chạy khỏe mạnh (`/api/v1/health` trả về `200 OK`).
2. Khởi chạy tunnel ngrok mới trỏ trực tiếp vào cổng 8003:
   ```bash
   ngrok http 8003
   ```
3. Lấy URL HTTPS được cấp (ví dụ: `https://xxxx-xxxx.ngrok-free.dev`).
4. Gửi URL này cho Dev 1 cùng `STAGING_DP_INBOUND_TOKEN` và hướng dẫn kèm header `X-Org-Code: NW-LOCAL-ORG-001`.
5. **Cấm:** Không public cổng 5434 (PostgreSQL) hoặc 8883 (MQTT) ra Internet.

---

## 7. Điều Kiện Tiên Quyết Để Chạy POST Thực Sang Dev 1 nextGRP

Để tránh làm sai lệch dữ liệu quản trị trên Dev 1, kiểm thử POST thực tế chỉ được kích hoạt khi:
1. Dev 1 đã chốt schema và trả lời mẫu ACK cho `receive_status` (IF-04).
2. Dev 1 đã sẵn sàng endpoint `POST /api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm` nhận form-urlencoded với trường `payload` là chuỗi JSON.
3. Dev 1 cung cấp URL HTTPS và cặp `API_KEY:API_SECRET` mới của Frappe v15.
4. Đặt biến môi trường double opt-in rõ ràng trước khi chạy runner:
   ```bash
   export DEV1_BASE_URL="https://dev1-frappe-instance.example.com"
   export DEV1_SITE_NAME="nextgrp.localhost"
   export DEV1_SERVICE_TOKEN="<API_KEY>:<API_SECRET>"
   export DEV1_E2E_ALLOW_POST="NW-IT-ONLY"
   export DEV1_E2E_APPROVED_DEVICES="NW-IT-TH001,NW-IT-DR001"
   python tests/e2e/dev1_dev2/runner.py --mode post
   ```

---

## 8. Bảng Tổng Hợp Endpoint và Trạng Thái Tích Hợp

| Chiều tích hợp | Giao diện | Endpoint | Phương thức | Trạng thái | Ghi chú kỹ thuật |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Dev 2 → Dev 1** | Get Config | `config_controller.get_device_config` | `GET` | **READY** | Query param `device_id`. Hỗ trợ S1/S6. |
| **Dev 2 → Dev 1** | Receive Status | `status_controller.receive_status` | `POST` | **PARTIAL / FAIL-CLOSED** | Transport form-urlencoded sẵn sàng. ACK schema chưa có xác nhận từ Dev 1. |
| **Dev 2 → Dev 1** | Receive Alarm | `alarm_controller.receive_alarm` | `POST` | **READY** | Form field `payload` chứa JSON string. Hỗ trợ occurrence, duplicate retry, cleared. |
| **Dev 2 → Dev 1** | Command Claim | `command_controller.claim_command` | `POST` | **PARTIAL** | Transport sẵn sàng. Chưa chốt payload cho S1/S6. |
| **Dev 2 → Dev 1** | Command ACK | `command_controller.ack_command` | `POST` | **PARTIAL** | Transport sẵn sàng. Chưa chốt payload cho S1/S6. |
| **Dev 1 → Dev 2** | Sync Org | `/api/v1/sync/organizations/{org_code}` | `PUT` | **READY** | Bearer auth + X-Org-Code. Đơn điệu phiên bản, duplicate detection. |
| **Dev 1 → Dev 2** | Sync Site | `/api/v1/sync/sites/{org_code}/{site_code}` | `PUT` | **READY** | Tương thích schema Dev 2. |
| **Dev 1 → Dev 2** | Sync Zone | `/api/v1/sync/zones/{org_code}/{site_code}/{zone_code}` | `PUT` | **READY** | Tương thích fallback DEFAULT zone. |
| **Dev 1 → Dev 2** | Sync Profile | `/api/v1/sync/profiles/{profile_code}` | `PUT` | **READY** | S1 (telemetry Cel, %RH), S6 (event DOOR_OPENED, DOOR_CLOSED). |
| **Dev 1 → Dev 2** | Sync Device | `/api/v1/sync/devices/{device_id}` | `PUT` | **READY** | Hỗ trợ NW-IT-TH001, NW-IT-DR001. |
| **Dev 1 → Dev 2** | Sync Rule | `/api/v1/sync/rules/{rule_id}` | `PUT` | **READY** | S1 threshold, S6 state change kèm lịch hoạt động (schedule). |
| **Dev 1 → Dev 2** | IF-06 Alarm State | `/api/v1/alarms/{alarm_key}/state` | `POST` | **READY** | Quản lý vòng đời New -> Acked -> In Progress -> Closed. Chặn STATE_REGRESSION. |
| **Dev 1 → Dev 2** | Query Latest | `/api/v1/devices/{device_id}/latest` | `GET` | **READY** | Đọc bản ghi mới nhất theo thiết bị. |
| **Dev 1 → Dev 2** | Query Telemetry | `/api/v1/devices/{device_id}/telemetry` | `GET` | **READY** | Chuỗi thời gian phục vụ hiển thị đồ thị Dev 1. |
| **Dev 1 → Dev 2** | Query Events | `/api/v1/devices/{device_id}/events` | `GET` | **READY** | Lịch sử sự kiện đóng/mở cửa của S6. |

---

## 9. Troubleshooting (Xử lý sự cố thường gặp)

1. **Trùng cổng `5434` hoặc `8003`:**
   - Kiểm tra tiến trình đang chiếm cổng:
     ```powershell
     Get-NetTCPConnection -LocalPort 8003,5434 -ErrorAction SilentlyContinue
     ```
   - Nếu cần đổi cổng staging, sửa `STAGING_DATA_API_PORT` và `STAGING_PG_PORT` trong `.secrets/staging.env`.
2. **Container `data-api` báo `Connection Refused` tới PostgreSQL:**
   - Đảm bảo service `postgres` đã healthy trước khi `data-api` kết nối (`depends_on: postgres: condition: service_healthy`).
3. **Lỗi `403 Forbidden` khi gọi Staging API:**
   - Kiểm tra `X-Org-Code` có khớp với `NW-LOCAL-ORG-001` (hoặc giá trị trong `STAGING_ALLOWED_ORGS`) hay không.
   - Kiểm tra token gửi trong header `Authorization: Bearer ...` có khớp chính xác với `STAGING_DP_INBOUND_TOKEN` trong `.secrets/staging.env`.
4. **Lỗi `409 STALE_VERSION` hoặc `VERSION_PAYLOAD_CONFLICT`:**
   - Đây là cơ chế bảo vệ cấu hình của Dev 2: Dev 1 gửi phiên bản cũ hơn phiên bản hiện có (stale) hoặc gửi cùng phiên bản nhưng nội dung payload bị thay đổi (conflict). Để cập nhật, Dev 1 cần tăng `version` lên tối thiểu `applied_version + 1`.


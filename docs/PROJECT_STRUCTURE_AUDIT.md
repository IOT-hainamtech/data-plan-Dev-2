# Project Structure Audit — nextWireless M1

Ngày rà soát: 2026-10-22 (Asia/Saigon)
Branch: `feat/multi-site-data-plane`
Commit được rà soát: `b206c4e`

## 1. Phạm vi và kết luận

Rà soát tĩnh bao phủ cây thư mục tracked, toàn bộ entrypoint Python, Docker Compose,
migration, test, script vận hành, firmware ESP32, README và tài liệu. README của branch
hiện tại được đối chiếu với `main`. Các đường dẫn Markdown nội bộ, tên file/lệnh được
kiểm tra trên cây thực tế; file tracked được quét tìm private key và mẫu credential phổ biến.

Đây là một PoC có cấu trúc hợp lý và có mức kiểm thử tốt hơn thông thường: các process
runtime được tách rõ, transaction alarm/outbox đúng hướng, migration cộng dồn, MQTT TLS/ACL,
firmware và tài liệu vận hành đều hiện hữu. Không có lý do kỹ thuật để tái cấu trúc toàn bộ
trước khi tích hợp nextGRP. Tuy vậy, cần xử lý một số boundary và nguồn cấu hình trùng lặp
trước khi xem nextGRP là tích hợp thật.

| Hạng mục | Đánh giá | Nhận xét ngắn |
| --- | --- | --- |
| Kiến trúc runtime | Tốt cho PoC | Process tách rõ, PostgreSQL là điểm phối hợp, outbox bền |
| Cấu trúc Python | Đạt, cần tách dần | Flat modules dễ hiểu nhưng `ingestor.py` đang kiêm quá nhiều vai trò |
| Database/migration | Chức năng tốt, quy trình còn rủi ro | Có migration cộng dồn nhưng có hai nguồn SQL và chưa có migration ledger |
| Test | Tốt | Unit, DB, MQTT, E2E, firmware contract; cách bố trí và automation còn có thể chuẩn hóa |
| Firmware | Tách module hợp lý | Driver/config/MQTT/payload rõ; provisioning vẫn gắn địa chỉ LAN cũ |
| README/tài liệu | Khá đầy đủ | Có Quick Start, kiến trúc, test, API, firmware, giới hạn; evidence đã lỗi thời một phần |
| Security hygiene | Tốt cho local PoC | Secret thực được ignore; mock token công khai có chủ ý; auth tenant chưa phải auth thật |
| Sẵn sàng tích hợp nextGRP | Chưa hoàn tất | Thiếu contract/auth chính thức, config sync và MQTT ACL lifecycle |

## 2. Kiến trúc phần mềm thực tế

Mô hình hiện tại là **modular monolith triển khai thành nhiều process/container**, dùng chung
PostgreSQL/TimescaleDB. Đây không phải microservice độc lập theo ownership dữ liệu vì các process
cùng đọc/ghi một schema và import chung module Python.

```text
ESP32 / simulator
  -> Mosquitto TLS + static ACL
  -> ingestor.py
       -> validation/profile registry
       -> telemetry/events/status (PostgreSQL/TimescaleDB)
       -> rule_engine.py (cùng transaction)
       -> alarm_occurrences + alarm_outbox
  -> outbox_worker.py -> mock_nextgrp.py / nextGRP adapter tương lai

FastAPI api.py -> PostgreSQL read model
rule_timer trong ingestor.py -> due rules + device timeout
migrate.py -> migration 002..008 khi Data Plane khởi động
```

### Thành phần và vai trò

| Thành phần | File/entrypoint | Vai trò và phụ thuộc chính |
| --- | --- | --- |
| MQTT ingestion | `app/ingestor.py:main`, `on_message`, `save_message` | MQTT TLS, validate topic/payload, lookup registry, ghi DB, gọi Rule Engine |
| Rule Engine | `app/rule_engine.py:on_telemetry`, `on_event`, `evaluate_due_rules` | State machine S1/S6, rule scope, alarm + outbox trong transaction của caller |
| Status timer | `app/ingestor.py:rule_timer`, `reconcile_device_timeouts` | Chạy cùng process ingestion, đánh offline và xử lý rule đến hạn |
| Data API | `app/api.py` | FastAPI read-only; query trực tiếp PostgreSQL; tenant context giả lập bằng `X-Org-Code` |
| Durable delivery | `app/outbox_worker.py` | Claim lease với `SKIP LOCKED`, retry/backoff, kiểm tra ACK, adapter HTTP |
| nextGRP mock | `app/mock_nextgrp.py` | Receiver local, idempotent ACK giả lập; không phải contract production |
| Simulator | `app/simulator.py` | Hai thiết bị demo cố định TH-001/DR-001 qua MQTT TLS |
| Migration runtime | `app/migrate.py` | Chạy lại migration 002–007 từ image và 008 từ volume mount |
| Fresh DB init | `db/001_schema.sql`…`008_multi_site_registry.sql` | Init theo thứ tự khi volume PostgreSQL trống |
| Firmware | `firmware/src/main.cpp` và các manager/driver | ESP-IDF/PlatformIO; NVS, Wi-Fi, NTP, MQTT, sensor, payload |
| Broker/security | `mosquitto/`, scripts sinh asset | TLS, password file, ACL tĩnh theo device/topic |
| Test | `app/test_*.py`, `firmware/test`, `firmware/tests` | Unit, DB integration, MQTT security, E2E, contract/provisioning |

Không phát hiện import vòng. Chuỗi phụ thuộc Python đáng chú ý là
`api.py` / `outbox_worker.py` / `migrate.py` / `check.py` → `ingestor.db_connection` →
`rule_engine.py`. Đây là phụ thuộc ngược vào entrypoint, không phải cycle nhưng làm tăng coupling.

## 3. Điểm đang làm tốt

- Tách container `data-plane`, `data-api`, `outbox-worker`, `mock-nextgrp`, `simulator`, MQTT và
  PostgreSQL rõ ràng trong `compose.yaml`.
- `rule_engine._write_occurrence()` ghi occurrence và outbox trong transaction ingestion; tránh
  mất alarm giữa xử lý rule và enqueue.
- `OutboxStore.claim()` có lease và `FOR UPDATE SKIP LOCKED`; retry/ACK/idempotency được tách thành
  lớp có unit/integration test.
- `device_id` toàn cục và hierarchy Organization/Site/Zone được giải thích rõ tại
  `docs/MULTI_ORG_SITE_DESIGN.md`; lựa chọn này nhất quán với topic, FK, dedup và runtime state.
- Migrations là additive, có test fresh/idempotency, TimescaleDB hypertable và index phục vụ các
  truy vấn chính.
- Firmware chia `ConfigManager`, `WifiManager`, `TimeSync`, `MqttManager`, driver, sequence và
  payload builder; không nhồi toàn bộ logic vào `main.cpp`.
- Secrets runtime nằm trong `.env`, `.secrets/`, certificate/NVS local và đều được `.gitignore`.
  Không phát hiện private key hoặc credential thực trong file tracked. `mock-development-token`
  là credential giả công khai cho receiver local và được mô tả đúng là mock.
- Tất cả liên kết Markdown nội bộ hiện resolve tới file tồn tại.
- Script vận hành có guard đáng kể: validate input, prompt SecureString, bảo vệ overwrite NVS,
  firewall mặc định preview-only, migration test dùng project/volume tách biệt.

## 4. Phát hiện cần sửa trước tích hợp nextGRP

| Mức | Phát hiện | Bằng chứng cụ thể | Khuyến nghị |
| --- | --- | --- | --- |
| Cao | Tenant header chưa phải authentication | `app/api.py:tenant_org`; caller tự đặt `X-Org-Code` | Thay bằng identity đã xác minh và org claims/RBAC theo contract nextGRP; giữ filter DB hiện tại như defense-in-depth |
| Cao | Adapter/ACK vẫn là contract giả | `app/outbox_worker.py:NextGrpAdapter`, `app/mock_nextgrp.py`, `NEXTGRP_ALLOW_INSECURE_MOCK` | Chốt endpoint, auth, idempotency, error/replay và schema version trước khi bật delivery thật |
| Cao | Registry nhiều tenant chưa đồng bộ lifecycle MQTT ACL | `mosquitto/acl` chỉ có ba device demo; `manage-mqtt-account.ps1` là thao tác thủ công | Xác định provisioning/revoke flow từ nextGRP tới broker; không coi insert `devices` là đã cấp quyền publish |
| Cao | Migration có hai nguồn và cơ chế 008 đặc biệt | `db/002..007` và `app/002..007`; `app/migrate.py` đọc 008 qua `/migrations`; `compose.yaml` mount riêng 008 | Chọn một thư mục migration canonical và mount/copy cả thư mục. Thêm kiểm tra hash/CI trong thời gian chuyển đổi |
| Trung bình | Chưa có ledger/version migration | `app/migrate.py` chạy lại 002–008 mỗi lần startup, dựa hoàn toàn vào idempotency | Dùng migration table/tool hoặc runner tối thiểu ghi checksum/version; bắt lỗi drift thay vì âm thầm chạy lại |
| Trung bình | README/test evidence lỗi thời sau migration 008 | `README.md:45,223–225` vẫn ghi 001–007, 54 app tests/63 tổng; `DATA_PLANE_STABILIZATION_REPORT.md` thuộc branch trước | Cập nhật evidence theo branch hiện tại hoặc ghi rõ báo cáo là historical snapshot; không dùng số cũ làm acceptance cho multi-site |
| Trung bình | IP provisioning không nhất quán và hiện không dùng được | README ghi host `192.168.1.110`/cert cũ `.87`; `ESP32_PROVISIONING.md`, `MQTT_LAN.md`, hai NVS template và test provisioning vẫn cố định `.87` | Chốt hostname/IP và cấp cert mới trước hardware; đưa broker URI thành input provisioning thay vì hard-code/test địa chỉ cũ |
| Trung bình | Khả năng profile-driven được mô tả rộng hơn implementation | `validate_telemetry_data()` chỉ enforce kiểu/range cho `number`; chưa enforce `required`, `allowed_values`, string/boolean; heartbeat còn dùng key `door_state` | Hoặc giới hạn tài liệu là hai profile M1, hoặc hoàn thiện validator generic trước khi nextGRP được phép tạo profile tùy ý |
| Trung bình | Cấu hình interval chưa là nguồn thống nhất | `devices.telemetry_interval_seconds`/`heartbeat_interval_seconds` ở migration 008; gap `120` hard-code trong `rule_engine.on_telemetry`; timeout nằm ở `device_status.timeout_seconds` | Chốt semantics với nextGRP và derive continuity/timeout từ registry/profile; tránh ba nguồn cấu hình độc lập |
| Trung bình | Multi-site API test chưa kiểm tra HTTP dependency/auth thật | `test_multi_site_integration.py` gọi trực tiếp `list_devices(self.org_a)` và `latest(...)` | Bổ sung test HTTP qua ASGI/TestClient hoặc container: thiếu header 401, cross-org 404, alarm/list isolation |
| Trung bình | Fresh migration test chưa bao phủ feature 008 | `scripts/test-migrations-fresh.ps1` chạy wildcard migration nhưng chỉ chạy test ingestion/rule cũ và assert constraint 005/006 | Thêm `test_multi_site_integration` và assert registry/scope constraints/index của 008 |

Hai bản `003_m1_rule_engine.sql` hiện có SHA-256 khác nhau; diff chỉ là comment/formatting và
không thay đổi semantics. Tuy nhiên chính sự khác hash này chứng minh duplication không được kiểm
soát và có thể drift thật ở lần sửa sau.

## 5. Nên cải thiện sau, không chặn tích hợp ban đầu

### Cấu trúc Python

`app/ingestor.py` có 351 dòng và đang chứa năm trách nhiệm: DB connection, contract validation,
repository SQL, MQTT lifecycle và background timer. `app/api.py` cũng lặp mẫu authorize-device /
open connection / serialize timestamp ở nhiều endpoint. Có thể tách dần, không big-bang:

```text
backend/
  pyproject.toml
  src/nextwireless/
    config.py
    db.py
    contracts.py
    repositories/
      devices.py
      telemetry.py
      outbox.py
    services/
      ingestion.py
      rules.py
      presence.py
    adapters/
      mqtt.py
      nextgrp.py
    api/
      app.py
      dependencies.py
      routes/
    workers/
      outbox.py
  tests/
    unit/
    integration/
    e2e/
migrations/
firmware/
deploy/compose.yaml
scripts/
docs/
```

Lợi ích: entrypoint mỏng, một `db.py` dùng chung, test boundaries rõ, adapter nextGRP thay thế được,
không ship test vào runtime image. Rủi ro: đổi imports, Docker build context/command, unittest
discovery và các script đang giả định working directory `/app`. Nên thực hiện theo các commit nhỏ,
giữ compatibility entrypoint, và chạy lại fresh DB + MQTT + E2E sau mỗi bước.

Các cải tiến nhỏ hơn:

- Chuyển `db_connection()` và `now_utc()` ra module hạ tầng chung; đây là bước tách ít rủi ro nhất.
- Đưa test khỏi `app/`; `app/Dockerfile` hiện `COPY *.py *.sql ./`, nên image runtime chứa toàn bộ test.
- Tách dependency runtime/dev và bổ sung `pyproject.toml` với formatter/linter/type-check/test config.
- Gom helper MQTT test `tls_context()`/`connected_client()` đang lặp ở
  `test_mqtt_security.py`, `test_multi_device_integration.py`, `test_e2e_simulator.py`.
- Tạo repository/query helper cho API để bỏ các câu kiểm tra device lặp và chuẩn hóa tenant filter.
- Bổ sung CI chạy syntax/unit, secret scan, migration fresh, firmware contract; integration Docker
  có thể là job riêng do chi phí cao.
- Cân nhắc healthcheck cho `data-plane`, `data-api`, `outbox-worker` thay vì chỉ `depends_on:
  service_started` ở một số đường phụ thuộc.
- Chuyển timestamp từ naive UTC sang `TIMESTAMPTZ` trong một migration được thiết kế riêng khi
  contract cho phép; không nên đổi vội vì ảnh hưởng rule/test/dữ liệu hiện có.
- Python firmware contract test hiện tái hiện validator riêng; nên có golden payload chung hoặc test
  trực tiếp output C++ để giảm nguy cơ hai implementation cùng “đúng” theo hai contract khác nhau.

## 6. Các phần không cần thay đổi lúc này

- Không cần chia thành nhiều repository hoặc database riêng cho từng process ở quy mô M1.
- Không cần thay PostgreSQL/TimescaleDB, MQTT QoS 1 hay durable outbox.
- Không cần thay `device_id` toàn cục; quyết định hiện tại nhất quán và đã được tài liệu hóa.
- Không cần đưa Organization/Site/Zone vào từng row telemetry; join qua registry tránh lặp metadata.
- Không cần thay FastAPI, Paho MQTT hoặc ESP-IDF chỉ vì cấu trúc đang flat.
- Không cần refactor firmware driver/manager: mức tách hiện tại phù hợp kích thước code.
- Simulator hard-code `demo/demo-site` là chấp nhận được nếu tiếp tục được ghi rõ là demo fixture.
- Giữ các báo cáo lịch sử như `DATA_PLANE_STABILIZATION_REPORT.md` là hợp lý; chỉ cần gắn nhãn
  snapshot rõ và không dùng số liệu cũ làm trạng thái hiện hành.

## 7. Đánh giá README

### Nội dung đã có và phù hợp

- Giới thiệu, trạng thái PoC và sơ đồ Mermaid.
- Bảng công nghệ và mô tả S1/S6, status, outbox, MQTT security, multi-organization.
- Quick Start thực thi được từ repository root: tạo `.env`, sinh MQTT asset, Compose up.
- Health/check/Timescale/outbox/test commands, firmware build, quản lý MQTT account.
- Danh sách endpoint, cảnh báo tenant header là mock, giới hạn production/hardware.
- Cấu trúc thư mục cấp cao và liên kết tới tài liệu chuyên sâu.
- Cảnh báo không xóa volume và không mở broker/firewall tùy ý.

### Nội dung chưa nhất quán hoặc còn thiếu

- Dòng tính năng `README.md:45` vẫn nói migration 001–007, trong khi phần khác nói 001–008.
- Mục kết quả `README.md:223–225` là evidence của branch trước, chưa gồm hai test multi-site và
  fresh migration 008. Cần ghi ngày/commit cho mọi số liệu để tránh hiểu là trạng thái động.
- Danh sách endpoint chỉ chú thích header bên cạnh `/devices` và `/alarms`, dù mọi endpoint dữ liệu
  device đều yêu cầu header. Phần văn bản dưới có nói đúng nhưng ví dụ dễ gây nhầm.
- Chưa có bảng cấu hình tập trung cho biến môi trường: bắt buộc, default, secret/non-secret và service
  sử dụng. Hiện người đọc phải ghép `compose.yaml`, `.env.example` và script sinh `.secrets`.
- Chưa có ví dụ `curl` tenant-aware và response/error 401/404.
- Mục cấu trúc chỉ ở cấp thư mục, chưa giải thích các entrypoint/process chính.
- Với một GitHub project public chuyên nghiệp, có thể bổ sung CI status, license, contribution/support
  policy; đây là governance improvement, không phải lỗi runtime.

### So sánh với `main`

`main` chỉ có 28 file tracked so với 89 file ở branch hiện tại. README `main` phản ánh giai đoạn cũ:
MQTT không TLS/port 1885, thiếu firmware, provisioning, status timeout, migration 005–008, full E2E
và multi-organization. README branch hiện tại đúng hơn đáng kể với code hiện tại. Vì vậy:

- Không nên lấy README `main` làm nguồn hướng dẫn vận hành cho branch này.
- Sau khi merge, README mới phải đi cùng code/migration/docs trong cùng thay đổi.
- Các điểm lỗi thời còn lại nằm ngay trong README branch hiện tại như liệt kê trên, chủ yếu do
  số liệu stabilization 001–007/54 tests chưa được refresh sau feature multi-site.

## 8. Kiểm tra link, lệnh, cấu hình và secrets

| Kiểm tra | Kết quả |
| --- | --- |
| Link Markdown tương đối | PASS — tất cả target tồn tại |
| Script được README nhắc đến | PASS — đều tồn tại |
| Compose services/entrypoint được nhắc đến | PASS |
| Firmware environments `sht31`, `reed_door` | PASS — có trong `platformio.ini` |
| `.env.example` | Không có secret thật; chỉ placeholder `CHANGE_ME...` |
| File tracked nhạy cảm | Không thấy `.env`, private key, MQTT CA private key, NVS local hoặc `.secrets` |
| Mock credential | Có `mock-development-token`, công khai có chủ ý và chỉ dùng mạng Compose local |
| Tính portable của lệnh | Chủ yếu Windows/PowerShell; phù hợp môi trường hiện tại nhưng README nói “terminal tương đương” mà chưa cung cấp full Linux workflow |

Lưu ý: `firmware/config/nvs.csv` có thể tồn tại cục bộ nhưng đã được ignore và không tracked; audit
không đưa nội dung secret local vào báo cáo.

## 9. Phân loại hành động

### Cần sửa trước tích hợp nextGRP

1. Chốt và triển khai auth/tenant claims + contract/ACK nextGRP thật.
2. Thiết kế lifecycle đồng bộ Organization/Site/Zone/Device/Profile/Rule và MQTT account/ACL.
3. Chọn một nguồn migration canonical, thêm version/checksum tracking và mở rộng fresh test cho 008.
4. Sửa README/evidence migration-test và đánh dấu report cũ là historical.
5. Chốt broker hostname/IP/certificate; bỏ hard-code `.87` khỏi provisioning workflow.
6. Chốt phạm vi profile generic; enforce đầy đủ schema hoặc giới hạn contract về hai profile M1.
7. Thống nhất sampling/heartbeat/timeout/continuity configuration.
8. Thêm automated HTTP tenant-isolation tests.

### Nên cải thiện sau

1. Tách `db.py`, config, repositories và entrypoints; sau đó chuyển sang package `src/`.
2. Đưa tests ra khỏi runtime image và chuẩn hóa unit/integration/e2e directories.
3. Bổ sung `pyproject.toml`, CI, lint/type checking và dependency groups.
4. Giảm helper/SQL/serialization lặp trong API và test.
5. Healthcheck/observability, retention/backup/benchmark và `TIMESTAMPTZ` roadmap.
6. Golden contract dùng chung giữa firmware và backend.

### Không cần thay đổi

1. Kiến trúc Compose nhiều process dùng chung PostgreSQL ở giai đoạn M1.
2. TimescaleDB, MQTT TLS/QoS 1, durable outbox và global `device_id`.
3. Phân rã module firmware hiện tại.
4. Cách telemetry tham chiếu registry bằng `device_id` thay vì sao chép location metadata.
5. Demo simulator và mock nextGRP, miễn là tiếp tục bị giới hạn rõ trong local PoC.

## 10. Thứ tự cải tiến ít rủi ro

1. Chỉ sửa tài liệu/evidence và thêm automated checks, không đổi runtime.
2. Hợp nhất migration source và thêm checksum/version test.
3. Tách `db_connection`/configuration khỏi `ingestor.py` với compatibility imports.
4. Đặt interface/contract chính thức cho nextGRP adapter và identity dependency.
5. Tích hợp config sync + MQTT ACL lifecycle trong môi trường test.
6. Sau khi integration ổn định mới cân nhắc chuyển package layout và tách tests/runtime image.

Thứ tự này giữ hệ thống đang chạy ổn định, đồng thời xử lý đúng các rủi ro có khả năng gây drift,
cross-tenant access hoặc sai contract khi nextGRP trở thành control plane thật.

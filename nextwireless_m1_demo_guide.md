# KỊCH BẢN DEMO nextWireless M1 — S1 & S6

**Loại:** Demo giả lập có điều khiển, môi trường staging ngày 30/10/2026.  
**Thiết bị S1:** `NW-IT-TH001` — môi trường (temperature, humidity).  
**Thiết bị S6:** `NW-IT-DR001` — cửa (door_state, DOOR_OPENED, DOOR_CLOSED).  
**Organization/Site:** `NW-LOCAL-ORG-001` / `NW-LOCAL-SITE-001`.  
**Data API:** `https://sequence-precision-bubbly.ngrok-free.dev` → staging 8003.  
**nextGRP:** `https://semisweet-conceal-gizzard.ngrok-free.dev`.

## A. Cách chạy trên PowerShell

1. Đặt `nextwireless_m1_demo_staging.ps1` trong `E:\nextwireless-dev2-postgres-timescale-prep` (không cần chỉnh source).
2. Mở PowerShell trong thư mục đó và chạy:

```powershell
cd E:\nextwireless-dev2-postgres-timescale-prep
. .\nextwireless_m1_demo_staging.ps1
Initialize-NWDemo
Get-NWDemoChecklist
```

`Initialize-NWDemo` yêu cầu nhập ẩn `API_KEY:API_SECRET` do Dev 1 cấp, đọc token staging từ `.secrets/staging.env`, kiểm tra Organization/Device/Rule và xác thực DB phải đúng `nextwireless_staging`. Token không được in ra. Các bước dưới đây đều chạy **thủ công theo thứ tự**, không tạo automation nền.

```powershell
Invoke-NWDemoStep 0   # PREP: S1/S6 ONLINE giả lập
Invoke-NWDemoStep 1   # S1 bình thường lần 1
Invoke-NWDemoStep 2   # S1 bình thường lần 2
Invoke-NWDemoStep 3   # S1 quá nhiệt occurrence 1
Invoke-NWDemoStep 4   # S1 quá nhiệt occurrence 2
Invoke-NWDemoStep 5   # S1 quá nhiệt occurrence 3 (có thể bỏ)
Invoke-NWDemoStep 6   # S6 mở cửa
Invoke-NWDemoStep 7   # S6 đóng cửa
Invoke-NWDemoStep 8   # S6 MỞ LẠI + giả lập cảnh báo cửa mở quá lâu occurrence 1
Invoke-NWDemoStep 9   # S6 giả lập cảnh báo cửa mở quá lâu occurrence 2 (có thể bỏ)
Invoke-NWDemoStep 10  # S1 OFFLINE giả lập
```

**Không** chạy tất cả 11 dòng một lúc. Người thuyết trình gọi từng dòng, Dev 1 refresh dashboard và xác nhận trước khi đi tiếp. S1/S6 `data_status` có thể chuyển `STALE` sau khoảng 120 giây nếu không cập nhật mới. `ONLINE` trong script là trạng thái kiểm thử trong hệ thống, không chứng minh thiết bị vật lý đang kết nối.

## B. Bảng diễn tiến và lời thuyết trình

| Bước | Thao tác | Kết quả mong đợi tại Dev 1 | Lời giới thiệu gợi ý |
|---|---|---|---|
| 0 | Mark S1, S6 online | Danh sách thiết bị Online | “Hai cảm biến đã được đăng ký và sẵn sàng giám sát.” |
| 1 | S1 T=27,50°C, RH=58,2% | S1 có mẫu nhiệt độ và độ ẩm, không alarm | “Dữ liệu môi trường nằm trong ngưỡng bình thường.” |
| 2 | S1 T=28,10°C, RH=60,5% | Có mẫu thứ hai, biểu đồ cập nhật | “Dữ liệu đang được cập nhật theo thời gian.” |
| 3 | S1 T=ngưỡng+1,5°C, RH=63% | `TEMPERATURE_HIGH`, occurrence_seq=1 | “Nhiệt độ vượt cấu hình, hệ thống tạo cảnh báo.” |
| 4 | S1 T=ngưỡng+3,2°C, RH=65% | Cùng Alarm, occurrence_count=2 | “Nhiệt độ tiếp tục tăng; số lần cảnh báo được ghi nhận.” |
| 5 | S1 T=ngưỡng+5,1°C, RH=67% | Cùng Alarm, occurrence_count=3 | “Mức bất thường tăng, vẫn được nhóm vào episode hiện tại.” |
| 6 | S6 `DOOR_OPENED` | latest.door_state=`open`, event mới | “Cảm biến phát hiện cửa mở.” |
| 7 | S6 `DOOR_CLOSED` | latest.door_state=`closed`, event mới | “Cảm biến phát hiện cửa đã đóng.” |
| 8 | S6 `DOOR_OPENED` lần hai, gửi cảnh báo demo | `DOOR_OPEN_TOO_LONG`, occurrence_seq=1 | “Ta chuyển sang kịch bản cửa bị để mở vượt giới hạn.” |
| 9 | Cảnh báo demo S6 thứ hai | Cùng Alarm S6, occurrence_count=2 | “Cảnh báo bất thường vẫn tiếp diễn trong cùng episode.” |
| 10 | S1 offline | Trạng thái thiết bị S1 offline; last_seen được giữ lại | “Cuối cùng, ngắt kết nối giả lập để kiểm tra quản lý trạng thái.” |

**Điều chỉnh quan trọng:** nếu Rule S1 có ngưỡng không phải 30°C, script **tự dùng ngưỡng đọc từ Frappe**, nên nhiệt độ hai mẫu bình thường có thể thấp hơn 27,50/28,10°C; ba mẫu quá ngưỡng luôn lớn hơn ngưỡng. Nếu muốn chứng minh Rule Engine tự phát hiện, cần chạy hệ thống ingestion/rule thật trong môi trường độc lập — script này không làm việc đó.

## C. Trường dữ liệu cần kiểm tra — KHÔNG được rỗng

### S1: telemetry bình thường và vượt ngưỡng

Mỗi bản tin measurement nguồn bao gồm:

```json
{
  "schema_version": 1,
  "device_id": "NW-IT-TH001",
  "boot_id": "<8 hex character / dynamic>",
  "seq": 1,
  "ts": "<current ISO 8601 UTC Z>",
  "ts_source": "ntp",
  "msg_kind": "measurement",
  "quality": "good",
  "data": {"temperature": 27.50, "humidity": 58.20}
}
```

`device_id`, `boot_id`, `seq`, `ts` phải có dữ liệu hợp lệ; `quality=good`; nhiệt độ °C, độ ẩm %RH; nhận diện đúng Profile `NW-IT-S1`, Site/Organization và thời gian Việt Nam khi hiển thị. Đây là cấu trúc MQTT gốc dùng làm **evidence** trong dữ liệu giả lập, không phải bản tin được publish lên broker. Mỗi đo nhiệt độ/độ ẩm sinh hai data point khi bảng staging hỗ trợ.

### S6: cửa mở / đóng

Ví dụ event:

```json
{
  "schema_version": 1,
  "device_id": "NW-IT-DR001",
  "boot_id": "a1b2c3d4",
  "seq": 9,
  "ts": "<current ISO 8601 UTC Z>",
  "event_id": "NW-IT-DR001-a1b2c3d4-9",
  "event_type": "DOOR_OPENED",
  "data": {"door_state": "open"}
}
```

Cùng lúc cập nhật snapshot trạng thái để API `/latest` trả `door_state=open`/`closed`. **Không dùng `/telemetry` để thay `/events` nếu mục tiêu là xem lịch sử chuyển trạng thái cửa.**

### Alarm S1 / S6: payload gửi sang nextGRP

```json
{
  "event_id": "<unique outer request id>",
  "org_code": "NW-LOCAL-ORG-001",
  "alarm_key": "<stable within episode>",
  "episode_key": "<exact same value as alarm_key>",
  "kind": "occurrence",
  "rule_id": "<actual rule_id from Dev1 config>",
  "rule_version": 1,
  "device_id": "NW-IT-TH001",
  "zone_code": "DEFAULT",
  "severity": "High",
  "episode_start": "<ISO 8601 UTC>",
  "raised_at": "<ISO 8601 UTC>",
  "occurrence_seq": 1,
  "event": {
    "event_id": "<unique source event id>",
    "event_type": "TEMPERATURE_HIGH",
    "ts": "<ISO 8601 UTC>",
    "data": {
      "temperature": 32.5,
      "humidity": 63,
      "threshold": 31,
      "operator": ">",
      "data_point": "temperature",
      "site_code": "NW-LOCAL-SITE-001",
      "zone_code": "DEFAULT",
      "profile_code": "NW-IT-S1",
      "test_source": "synthetic-staging",
      "summary": "SIMULATED temperature warning"
    }
  }
}
```

Các số `rule_version`, `severity`, `threshold` trong JSON trên chỉ là **ví dụ về kiểu dữ liệu**, không được coi là cấu hình đã xác nhận; PowerShell lấy Rule/threshold thực tế từ Dev 1. S6 dùng tương tự nhưng `device_id=NW-IT-DR001`, `event_type=DOOR_OPEN_TOO_LONG`, `event.data.door_state=open`, `threshold_seconds`, `simulated_open_seconds`, `duration_evidence` và `test_source=synthetic-staging`.

**Idempotency:** outer `event_id` duy nhất mỗi request; nested `event.event_id` riêng; `episode_key=alarm_key`; `occurrence_seq` tăng 1→2→3 trong cùng episode. Không đổi `event_id` để retry một request không chắc đã được nhận; phải kiểm tra Inbox/ACK trước.

### Offline S1

Payload trạng thái nghiệp vụ nếu chuyển theo IF-04 chuẩn:

```json
{
  "event_id": "<unique status transition id>",
  "org_code": "NW-LOCAL-ORG-001",
  "device_id": "NW-IT-TH001",
  "state": "offline",
  "since": "<current ISO 8601 UTC>",
  "last_seen": "<last successful heartbeat>",
  "reason": "demo_disconnect",
  "fw_version": "<actual firmware version if known>"
}
```

**Điểm khác của API Dev 1 đang dùng:** `status_controller.receive_status` hiện nhận `device_id` và `state` dạng form. Script gọi đúng hai trường được xác nhận; `since`, `last_seen`, `reason`, `fw_version` là các trường của event chuẩn dành cho tích hợp đầy đủ, không khẳng định Dev 1 sẽ lưu các trường đó qua API hiện tại. Script đồng thời cập nhật cột kết nối của thiết bị **staging** (không đụng nguồn thật), để trạng thái hiển thị qua API 8003 phù hợp.

## D. Kiểm tra sau từng bước

```powershell
# Dùng script đã Initialize-NWDemo:
Get-NWDemoLatest NW-IT-TH001
Get-NWDemoLatest NW-IT-DR001

# S1 qua Data API staging:
Invoke-RestMethod -Uri 'https://sequence-precision-bubbly.ngrok-free.dev/api/v1/devices/NW-IT-TH001/telemetry?limit=20' -Headers $script:NWDemo.DPHeaders | ConvertTo-Json -Depth 7

# S6 qua Data API staging:
Invoke-RestMethod -Uri 'https://sequence-precision-bubbly.ngrok-free.dev/api/v1/devices/NW-IT-DR001/events?limit=20' -Headers $script:NWDemo.DPHeaders | ConvertTo-Json -Depth 7
```

Gọi Dev 1 kiểm tra **HTTP status + response** trong DevTools Network, không chỉ dựa vào màn hình. Nếu `/latest` đã có dữ liệu nhưng Dashboard trống, kiểm tra mapping, proxy, trạng thái STALE và polling. Nếu SQL schema S1 khác kỳ vọng, script **dừng an toàn**, không cố chèn dữ liệu vào bảng không xác định.

## E. Ranh giới kiểm thử

- `nextwireless_staging` / `5434` / `8003` là phạm vi duy nhất được phép ghi phía Dev 2.
- Script có POST Alarm/Status tới nextGRP Dev 1: phải thống nhất đây là **dữ liệu test** và không coi như cảnh báo thiết bị thật.
- **Không** chạy trên 8002, 5432, broker 8883 hay thiết bị ESP32 thật.
- Các cảnh báo được **giả lập và POST trực tiếp sang Frappe**, không được sinh tự động bởi Rule Engine trong staging chỉ có Postgres và data-api.
- Tình huống “mở cửa quá lâu” không dùng thời lượng đo thật: script gửi evidence ghi rõ `synthetic-staging`/`simulated_open_seconds`. Nếu muốn demo đúng Rule Engine, phải có test ingestion chạy và chờ `open_limit_s` thật hoặc cấu hình ngưỡng demo được phê duyệt.

Sau demo, chạy `Clear-NWDemoSession` để xóa các token trong biến phiên PowerShell.

# 3. Thông tin kết nối

## 3.1. Địa chỉ API

Management Plane hiện chạy local tại:

http\://nextgrp.localhost:8000

Khi Data Plane chạy trên máy khác trong cùng mạng LAN, sử dụng:

http\://\<WINDOWS_LAN_IP>:8000

Cổng `8000` được Windows portproxy chuyển tiếp đến Frappe chạy trong WSL2.

Đường dẫn API nghiệp vụ dùng tiền tố:

/api/method/nextgrp.nextgrp_iot.controller.

Dev 2 không sử dụng `localhost` hoặc `nextgrp.localhost` như địa chỉ mạng đích nếu các hostname đó không trỏ về máy Management Plane.

## 3.2. HTTP headers

Mọi request từ Data Plane gửi kèm:

Authorization: token \<API_KEY>:\<API_SECRET>

X-Frappe-Site-Name: nextgrp.localhost

Accept: application/json

Với những API POST hiện tại, request sử dụng `application/x-www-form-urlencoded` để truyền các tham số của whitelisted method.

Ví dụ:

Content-Type: application/x-www-form-urlencoded

Đặc biệt, API `receive_alarm` nhận tham số tên `payload` chứa một chuỗi JSON, không phải nhận trực tiếp object JSON ở root của HTTP body.

## 3.3. Cấu hình môi trường Data Plane

M1_FRAPPE_BASE_URL=http\://\<WINDOWS_LAN_IP>:8000

M1_FRAPPE_SITE=nextgrp.localhost



M1_FRAPPE_API_KEY=\<SECRET_FROM_MANAGEMENT>

M1_FRAPPE_API_SECRET=\<SECRET_FROM_MANAGEMENT>

API Key/Secret được lưu ở máy hoặc service Data Plane, tuyệt đối không lưu trong React frontend và không commit vào Git.

Môi trường demo hiện sử dụng HTTP nội bộ LAN. Nếu kết nối qua Internet hoặc mạng không tin cậy, bắt buộc chuyển sang HTTPS hoặc VPN trước khi trao đổi token.

# 4. Định danh và đồng bộ dữ liệu

Hai bên phải thống nhất các mã định danh sau:

| **Trường**   | **Ý nghĩa**                               |
| ------------ | ----------------------------------------- |
| org_code     | Mã Organization dùng trong hợp đồng Alarm |
| site_code    | Mã Site quản lý thiết bị                  |
| device_id    | Mã Device được đăng ký trong nextGRP      |
| profile_code | Mã Device Profile                         |
| rule_id      | Mã Rule Configuration                     |
| rule_version | Phiên bản Rule                            |
| event_id     | ID duy nhất của sự kiện gửi vào Alarm API |
| alarm_key    | ID của một đợt cảnh báo                   |
| command_id   | ID duy nhất của yêu cầu điều khiển        |

Các mã đang sử dụng trong môi trường test Management Plane:

Organization : NW-LOCAL-ORG-001

Site         : NW-LOCAL-SITE-001

Device       : NW-LOCAL-TH001

Profile      : NW-LOCAL-S1

Rule         : NW-LOCAL-RULE-S1

Rule Version : 1

Đây là mã của môi trường demo, không mặc định là mã của thiết bị vật lý Dev 2.

**Yêu cầu bắt buộc:** Dev 2 phải xác nhận ánh xạ giữa Device ID trong MQTT, firmware, database Data Plane và `Device.device_id` trên nextGRP. Không được tự giả định `DR-001`, `NW-TEST-DR001` và `NW-LOCAL-TH001` là cùng một thiết bị.

# 5. API Management Plane cung cấp cho Data Plane

## 5.1. Danh sách endpoint

| **STT** | **Method** | **Endpoint**                        | **Chức năng**                    |
| ------- | ---------- | ----------------------------------- | -------------------------------- |
| 1       | GET        | config_controller.get_device_config | Đọc cấu hình Device/Profile/Rule |
| 2       | POST       | status_controller.receive_status    | Cập nhật trạng thái kết nối      |
| 3       | POST       | alarm_controller.receive_alarm      | Gửi Alarm occurrence/cleared     |
| 4       | POST       | command_controller.claim_command    | Nhận Command đang chờ            |
| 5       | POST       | command_controller.ack_command      | Xác nhận kết quả Command         |

Mọi endpoint trên đều thuộc prefix:

/api/method/nextgrp.nextgrp_iot.controller.

Các API được xây dựng bằng Frappe whitelisted methods và trả response bên trong trường `message`.



---



# 6. API 01 — GET Device Configuration

**Endpoint:**

GET /api/method/nextgrp.nextgrp_iot.controller.config_controller.get_device_config

**Query parameter:**

| **Tên**   | **Kiểu** | **Bắt buộc** | **Ý nghĩa**                       |
| --------- | -------- | ------------ | --------------------------------- |
| device_id | string   | Có           | Mã thiết bị trên Management Plane |

**Ví dụ:**

GET /api/method/nextgrp.nextgrp_iot.controller.config_controller.get_device_config?device_id=NW-LOCAL-TH001

**Response cấu trúc:**

{

  "message": {

    "device": {

      "device_id": "NW-LOCAL-TH001",

      "device_profile": "NW-LOCAL-S1",

      "site": "NW-LOCAL-SITE-001",

      "zone": null,

      "status": "Active",

      "connectivity_type": "WIFI",

      "config_version": 1,

      "online_state": "Online",

      "last_seen": "2026-10-29 10:20:23.159474"

    },

    "site": {

      "site_code": "NW-LOCAL-SITE-001",

      "organization": "NW-LOCAL-ORG-001",

      "timezone": "Asia/Ho_Chi_Minh"

    },

    "zone": null,

    "profile": {

      "profile_code": "NW-LOCAL-S1",

      "version": 1,

      "protocol": "MQTT",

      "supports_telemetry": 0,

      "supports_events": 0,

      "supports_commands": 1,

      "data_points": [],

      "event_types": [],

      "commands": [

        {

          "command_code": "PING",

          "command_name": "Ping Device",

          "parameter_schema": null,

          "risk_level": "Low",

          "requires_approval": 0

        }

      ]

    },

    "rules": [

      {

        "rule_id": "NW-LOCAL-RULE-S1",

        "rule_type": "THRESHOLD",

        "scope_type": "Device",

        "scope_ref": "NW-LOCAL-TH001",

        "data_point": "temperature",

        "operator": ">",

        "threshold": 30.0,

        "severity": "High",

        "active": 1,

        "version": 1,

        "auto_close": 1

      }

    ]

  }

}

Ví dụ trên chỉ thể hiện các field chính; response thực tế có thêm thông tin Device, Site, Profile và Rule.

**Quy tắc xử lý:**

- Chỉ thiết bị thuộc Organization được cấp quyền mới được trả cấu hình.
- `device.status = Active` xác định thiết bị đang hoạt động theo cấu hình quản lý.
- `online_state` là trạng thái kết nối ghi nhận gần nhất.
- `profile.data_points` định nghĩa các chỉ số telemetry hỗ trợ.
- `profile.event_types` định nghĩa các loại sự kiện thiết bị.
- `profile.commands` định nghĩa các lệnh hỗ trợ.
- `rules` là danh sách Rule đang active và áp dụng cho Device.

Data Plane phải sử dụng `rule_id` và `version` được trả về khi gửi Alarm.

**Lưu ý cần xử lý trước demo S1:** dữ liệu test hiện tại vẫn có `supports_telemetry = 0` và `data_points = []`, dù Rule tham chiếu `temperature`. Management Plane phải hoàn thiện Profile S1 trước khi coi cấu hình telemetry đã sẵn sàng.

**Trạng thái kiểm thử:** HTTP 200 PASS với token `IoT Data Plane`.



---



# 7. API 02 — POST Device Status

**Endpoint:**

POST /api/method/nextgrp.nextgrp_iot.controller.status_controller.receive_status

**Body dạng form:**

device_id=NW-LOCAL-TH001

state=online

| **Field** | **Kiểu** | **Bắt buộc** | **Giá trị**             |
| --------- | -------- | ------------ | ----------------------- |
| device_id | string   | Có           | Device ID đã đăng ký    |
| state     | string   | Có           | `online` hoặc `offline` |

**Quy tắc nghiệp vụ:**

- `online` được ghi vào Device dưới dạng `Online`.
- `offline` được ghi dưới dạng `Offline`.
- Khi nhận Online, backend cập nhật `last_seen` theo thời gian server.
- Khi nhận Offline, backend giữ nguyên `last_seen`.
- Khi trạng thái thay đổi, backend phát realtime event `nextgrp_iot_device_status`.
- Data Plane không được gửi trạng thái cho Device ngoài Organization đã cấp.

**Response:** Frappe trả JSON trong `message`. Các field trạng thái trả về cần được xác nhận bằng bài kiểm thử token cuối cùng.

**Giới hạn hiện tại:** API chưa có `event_id`, timestamp nguồn và sequence number. Vì vậy, chưa có cơ chế chống trường hợp trạng thái cũ đến muộn và ghi đè trạng thái mới.

**Trạng thái kiểm thử:** Đã PASS nghiệp vụ bằng Administrator, chưa hoàn tất POST bằng token Data Plane.



---



# 8. API 03 — POST Alarm

**Endpoint:**

POST /api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm

**Body dạng form:**

payload=\<JSON_STRING>

## 8.1. Payload occurrence

{

  "event_id": "DP-M1-ALARM-EVT-000001",

  "org_code": "NW-LOCAL-ORG-001",

  "alarm_key": "DP-M1-ALARM-EPISODE-000001",

  "episode_key": "DP-M1-ALARM-EPISODE-000001",

  "kind": "occurrence",

  "rule_id": "NW-LOCAL-RULE-S1",

  "rule_version": 1,

  "device_id": "NW-LOCAL-TH001",

  "severity": "High",

  "raised_at": "2026-10-29T14:00:00+07:00",

  "occurrence_seq": 1,

  "event": {

    "event_id": "DP-M1-SOURCE-EVT-000001",

    "event_type": "TEMPERATURE_HIGH",

    "ts": "2026-10-29T14:00:00+07:00",

    "data": {

      "temperature": 31.5,

      "humidity": 72.3,

      "summary": "Temperature exceeds configured threshold"

    }

  }

}

## 8.2. Ý nghĩa các field

| **Field**        | **Ý nghĩa**                             |
| ---------------- | --------------------------------------- |
| event_id         | ID duy nhất của request/sự kiện Alarm   |
| org_code         | Organization tương ứng với Device       |
| alarm_key        | Định danh một episode cảnh báo          |
| episode_key      | Định danh episode do Data Plane quản lý |
| kind             | `occurrence` hoặc `cleared`             |
| rule_id          | Rule phát sinh cảnh báo                 |
| rule_version     | Version của Rule                        |
| device_id        | Thiết bị phát sinh cảnh báo             |
| severity         | `Low`, `Medium`, `High`, `Critical`     |
| raised_at        | Thời điểm phát sinh sự kiện, ISO 8601   |
| occurrence_seq   | Thứ tự occurrence trong cùng episode    |
| event.event_id   | ID sự kiện nguồn từ Data Plane          |
| event.event_type | Loại sự kiện nguồn                      |
| event.ts         | Timestamp nguồn                         |
| event.data       | Dữ liệu/evidence liên quan              |

## 8.3. Quy tắc chống xử lý trùng

Management Plane có `IoT Event Inbox` để lưu `event_id`, payload hash và kết quả xử lý.

Data Plane cần tuân thủ:

1. Mỗi event mới phải có `event_id` duy nhất.
2. Khi retry cùng event, giữ nguyên `event_id` và toàn bộ payload.
3. Không tạo `event_id` mới chỉ vì HTTP timeout.
4. Một Alarm episode giữ nguyên `alarm_key`.
5. `occurrence_seq` bắt đầu từ 1 và tăng liên tiếp: 1, 2, 3...
6. Không được gửi số thứ tự nhảy cóc.
7. Khi cùng `event_id` được gửi lại với payload khác, backend từ chối.
8. Một episode đã clear/close không được mở lại bằng cách tái sử dụng Alarm cũ.

Ví dụ response lần đầu:

{

  "message": {

    "alarm_key": "DP-M1-ALARM-EPISODE-000001",

    "kind": "occurrence",

    "status": "New",

    "occurrence_count": 1,

    "changed": true,

    "duplicate": false

  }

}

Ví dụ response retry:

{

  "message": {

    "alarm_key": "DP-M1-ALARM-EPISODE-000001",

    "occurrence_count": 1,

    "changed": false,

    "duplicate": true

  }

}

Đây là các trường phản hồi chính đã được kiểm thử; response thực tế có thể chứa thêm field.

## 8.4. Cleared event

Khi điều kiện cảnh báo đã hết, Data Plane gửi một event mới với:

-

```
kind = cleared
```

1. `event_id` mới
2. `alarm_key` và `episode_key` giữ nguyên
3. `cleared_at` là timestamp hợp lệ
4. Giữ đúng `rule_id`, `rule_version`, `device_id` và dữ liệu liên quan theo contract

Ví dụ minh họa phần thay đổi:

{

  "event_id": "DP-M1-ALARM-EVT-000002",

  "org_code": "NW-LOCAL-ORG-001",

  "alarm_key": "DP-M1-ALARM-EPISODE-000001",

  "episode_key": "DP-M1-ALARM-EPISODE-000001",

  "kind": "cleared",

  "rule_id": "NW-LOCAL-RULE-S1",

  "rule_version": 1,

  "device_id": "NW-LOCAL-TH001",

  "severity": "High",

  "cleared_at": "2026-10-29T14:05:00+07:00",

  "event": {

    "event_id": "DP-M1-SOURCE-EVT-000002",

    "event_type": "TEMPERATURE_NORMAL",

    "ts": "2026-10-29T14:05:00+07:00",

    "data": {

      "summary": "Temperature returned to normal"

    }

  }

}

Ví dụ này cần được xác nhận lại với validation của AlarmService khi kiểm thử cleared qua token, đặc biệt các field thời gian bắt buộc.

Nếu `Rule.auto_close = 1`, backend có thể chuyển Alarm sang `Closed` sau cleared theo nghiệp vụ đã xây.

**Trạng thái kiểm thử:** Occurrence, cleared, retry, concurrency và Event Inbox đã PASS với Administrator; chưa hoàn tất POST bằng token Data Plane.



---



# 9. API 04 — POST Claim Command

**Endpoint:**

POST /api/method/nextgrp.nextgrp_iot.controller.command_controller.claim_command

**Body:**

device_id=NW-LOCAL-TH001

Data Plane gọi API này để lấy một lệnh đang chờ dành cho Device.

**Response khi có lệnh:**

{

  "message": {

    "command": {

      "command_id": "CMD-example-000001",

      "device_id": "NW-LOCAL-TH001",

      "command_code": "PING",

      "parameters": {},

      "delivery_token": "\<OPAQUE_DELIVERY_TOKEN>",

      "expires_at": "2026-10-29 14:10:00",

      "lease_until": "2026-10-29 14:01:00",

      "attempt_count": 1

    }

  }

}

Các timestamp và ID ở ví dụ chỉ mang tính minh họa.

**Response khi không có lệnh:**

{

  "message": {

    "command": null

  }

}

**Quy tắc:**

- Command ban đầu có trạng thái `Pending`.
- Khi Data Plane claim, trạng thái thành `Dispatched`.
- Backend cấp `delivery_token` riêng cho lần cấp phát đó.
- Lease mặc định trong code hiện tại: 60 giây.
- Command được tạo với thời hạn mặc định 10 phút.
- Khi hết lease mà chưa có ACK, lệnh có thể được cấp phát lại nếu chưa hết hạn.
- Mỗi lần cấp phát lại có thể phát sinh `delivery_token` mới.

**Yêu cầu bắt buộc với Dev 2:** sử dụng `command_id` làm khóa chống thực thi trùng. Việc claim lại không có nghĩa được phép thực thi lại một lệnh đã được thiết bị hoàn thành.

**Trạng thái kiểm thử:** Đã PASS bằng Administrator trước khi khóa Controller; chưa kiểm thử lại bằng token Data Plane.



---



# 10. API 05 — POST Command ACK

**Endpoint:**

POST /api/method/nextgrp.nextgrp_iot.controller.command_controller.ack_command

**Body form:**

command_id=CMD-example-000001

delivery_token=\<TOKEN_RETURNED_BY_CLAIM>

status=Succeeded

result_json={"device_ack":true}

| **Field**      | **Kiểu**    | **Bắt buộc**    |
| -------------- | ----------- | --------------- |
| command_id     | string      | Có              |
| delivery_token | string      | Có              |
| status         | string      | Có              |
| result_json    | JSON string | Có thể bỏ trống |

`status` chỉ nhận:

- `Succeeded`: thiết bị thực sự xác nhận thực thi thành công.
- `Failed`: thiết bị xác nhận thất bại hoặc Data Plane xác định được thất bại cuối cùng.

Ví dụ ACK:

{

  "command_id": "CMD-example-000001",

  "delivery_token": "\<OPAQUE_DELIVERY_TOKEN>",

  "status": "Succeeded",

  "result_json": "{\\"device_ack\\":true}"

}

**Response thành công:**

{

  "message": {

    "command_id": "CMD-example-000001",

    "status": "Succeeded",

    "duplicate": false

  }

}

Khi gửi lại đúng ACK, backend trả `duplicate = true`.

**Quy tắc:**

1. Data Plane không được tự ACK `Succeeded` chỉ vì MQTT publish thành công.
2. `Succeeded` cần dựa trên ACK hoặc bằng chứng thực thi từ thiết bị.
3. ACK phải dùng đúng `delivery_token` được trả về khi claim.
4. Token cũ sau khi cấp phát lại có thể bị từ chối.
5. ACK trùng phải giữ nguyên status và result.
6. Nếu kết quả thực thi chưa xác định, Data Plane không được tự giả định thành công; cần xử lý trạng thái chưa chắc chắn và retry theo chính sách thống nhất.

**Trạng thái kiểm thử:** ACK Failed và retry đã PASS trong bài kiểm thử mô phỏng; chưa có bằng chứng ESP32 thực thi thật.



# 13. Hợp đồng yêu cầu phía Data Plane cung cấp ngược lại

Phần này **chưa được phía Data Plane xác nhận**. Các đường dẫn và cấu trúc bên dưới là những hạng mục cần Dev 2 điền thông tin thực tế.

## 13.1. S1 Latest

**Mục đích:** React Dashboard lấy nhiệt độ/độ ẩm mới nhất.

Phía Dev 2 cần cung cấp:

- HTTP endpoint hoặc cơ chế truy vấn tương đương.
- Phương thức xác thực.
- Query theo `device_id`.
- Thời điểm đo, thời điểm nhận và trạng thái stale.
- JSON response có giá trị temperature/humidity.

Cấu trúc JSON đề xuất để thống nhất:

{

  "device_id": "NW-LOCAL-TH001",

  "timestamp": "2026-10-29T14:00:00+07:00",

  "metrics": {

    "temperature": 31.5,

    "humidity": 72.3

  }

}

Đây chưa phải response thực tế đã được xác nhận.

## 13.2. S1 Telemetry History

**Mục đích:** Vẽ biểu đồ nhiệt độ/độ ẩm.

Cần xác nhận:

- Endpoint.
- Tham số `device_id`, `from`, `to`, `limit`.
- Dữ liệu sắp xếp theo thời gian tăng/giảm.
- Đơn vị đo, timezone, retention.
- Phân trang hoặc giới hạn số mẫu.

Response đề xuất:

{

  "device_id": "NW-LOCAL-TH001",

  "points": [

    {

      "ts": "2026-10-29T14:00:00+07:00",

      "temperature": 31.5,

      "humidity": 72.3

    }

  ]

}

## 13.3. S6 Latest State

**Mục đích:** Hiển thị trạng thái cửa.

Response đề xuất:

{

  "device_id": "\<S6_DEVICE_ID>",

  "timestamp": "2026-10-29T14:00:00+07:00",

  "state": "open"

}

Cần thống nhất các giá trị trạng thái như `open`, `closed`, `unknown`, cách biểu diễn trạng thái mất tín hiệu và quy tắc stale.

## 13.4. S6 Event History

**Mục đích:** Hiển thị lịch sử mở/đóng cửa.

Response đề xuất:

{

  "device_id": "\<S6_DEVICE_ID>",

  "events": [

    {

      "event_id": "DP-S6-EVT-001",

      "event_type": "DOOR_OPENED",

      "ts": "2026-10-29T14:00:00+07:00"

    },

    {

      "event_id": "DP-S6-EVT-002",

      "event_type": "DOOR_CLOSED",

      "ts": "2026-10-29T14:00:15+07:00"

    }

  ]

}

Cần xác nhận event schema thực tế, query time range, pagination và retention.

## 13.5. MQTT Command/ACK Contract

Đề nghị Dev 2 cung cấp:

- MQTT broker endpoint (không gửi credential trong tài liệu chung).
- Topic telemetry, event, command và ACK.
- QoS đang sử dụng.
- Payload của Command.
- Payload ACK từ firmware.
- Correlation bằng `command_id`.
- Timeout thực thi.
- Cơ chế chống duplicate.
- Chính sách reconnect và lệnh gửi lại.
- Danh sách `command_code` ESP32 thực sự hỗ trợ.

Các MQTT topic phải lấy từ Data Plane thực tế; Management Plane không tự đặt topic rồi coi đó là contract đã thống nhất.

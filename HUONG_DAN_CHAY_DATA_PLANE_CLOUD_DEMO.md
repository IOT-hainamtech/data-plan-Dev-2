# Hướng dẫn chạy Data Plane Dev 2 Cloud Demo trên Ubuntu

**Bản:** `v2026.11.09-cloud-demo` · **Máy chủ:** Ubuntu `x86_64/amd64`  
**GitHub private:** https://github.com/hainamglobal/nextWirelessDataPlane  
**Release:** https://github.com/hainamglobal/nextWirelessDataPlane/releases/tag/v2026.11.09-cloud-demo

## 1. Chuẩn bị server

- Đã cài **Docker Engine** và **Docker Compose v2**; tài khoản triển khai có quyền chạy Docker.
- Server còn đủ tài nguyên cho bản **Standalone**: tổng giới hạn RAM các container **1.376 MiB**; nên có khoảng **2 GiB RAM khả dụng cho dự án** và dung lượng ổ đĩa dự phòng (khuyến nghị ban đầu 10 GiB). Kiểm tra tài nguyên của các ứng dụng khác trên server dùng chung.
- Có quyền truy cập GitHub private; quản trị viên chuẩn bị mật khẩu, token, địa chỉ mạng và chứng chỉ MQTT/TLS **ngoài GitHub**.

Kiểm tra:

```bash
uname -m                  # x86_64
docker --version
docker compose version
free -h
df -h
```

## 2. Tải Docker Release

Tải file `nextwireless-dev2-cloud-demo-20261109-linux-amd64.tar.gz` từ trang Release ở trên (có thể tải trên máy đã đăng nhập GitHub rồi chuyển lên server).

Hoặc nếu server đã cài GitHub CLI (`gh`) và tài khoản có quyền truy cập:

```bash
gh auth login
gh release download v2026.11.09-cloud-demo \
  --repo hainamglobal/nextWirelessDataPlane \
  --pattern 'nextwireless-dev2-cloud-demo-20261109-linux-amd64.tar.gz'
```

Kiểm tra SHA-256 (phải khớp tuyệt đối):

```bash
echo '115088eb4639e91f73d365b21c1888ec6dd792019122e9460e204d77a8f6542e  nextwireless-dev2-cloud-demo-20261109-linux-amd64.tar.gz' | sha256sum -c -
```

Nếu báo `FAILED`, **dừng triển khai**.

## 3. Giải nén và cấu hình

```bash
tar -xzf nextwireless-dev2-cloud-demo-20261109-linux-amd64.tar.gz
cd nextwireless-dev2-cloud-demo-20261109-linux-amd64
sha256sum -c SHA256SUMS

cp deploy.env.example .env
chmod 600 .env
mkdir -p .secrets mosquitto/certs
cp deploy/examples/mqtt-broker.env.example .secrets/mqtt-broker.env
cp deploy/examples/mqtt-data-plane.env.example .secrets/mqtt-data-plane.env
chmod 600 .secrets/*.env
```

Dùng trình soạn thảo để thay mọi giá trị `CHANGE_ME` trong `.env` và `.secrets/*.env` bằng thông tin thật do quản trị viên cung cấp. Theo README trong gói, cần đặc biệt kiểm tra **PostgreSQL password**, **Data API inbound token**, **MQTT credentials**, **`MQTT_BIND_IP`** và các địa chỉ mạng. Giữ `DEPLOY_MODE=standalone` cho bản demo 2 GiB RAM.

Đặt chứng chỉ MQTT tại `mosquitto/certs/` theo README (CA, server certificate và private key); SAN của chứng chỉ phải khớp tên miền/IP mà cảm biến sử dụng. Giới hạn quyền truy cập private key. Không đẩy các file cấu hình thật lên Git.

## 4. Khởi chạy

Trong thư mục vừa giải nén, chạy **một lệnh**:

```bash
bash deploy.sh
```

Script sẽ kiểm tra cấu hình, nạp Docker image từ archive và khởi động bốn service: **PostgreSQL/TimescaleDB, MQTT Mosquitto, Data Plane, Data API**. Không cần tự build source.

## 5. Xác minh hoạt động

```bash
docker compose -p nextwireless-dev2 -f compose.yaml -f compose.deploy.yaml ps
curl -fsS http://127.0.0.1:8002/api/v1/health
docker compose -p nextwireless-dev2 -f compose.yaml -f compose.deploy.yaml logs --tail=100 data-plane data-api mqtt postgres
docker stats --no-stream
```

**Đạt:** 4 service `healthy`; API trả `status: UP`, `database: UP`. Nếu project name thực tế trong `deploy.sh` khác `nextwireless-dev2`, sử dụng đúng tên do script in ra để xem trạng thái/log.

Cuối cùng, cấu hình **S1/S6 gửi MQTT/TLS đến IP hoặc DNS của server qua cổng 8883**, kiểm tra dữ liệu trên Data API. Mở đúng đường mạng được quản trị viên phê duyệt; không công khai PostgreSQL 5432. Nếu **Dev 1** chạy trên máy khác, cần cấu hình đường truy cập an toàn đến Data API và xác thực; `127.0.0.1:8002` chỉ dùng ngay trên server. Bản **Standalone không tự đẩy** Status/Event/Alarm sang Dev 1.

**Lưu ý:** Telemetry Demo giữ ngưỡng 2 giờ, scheduler kiểm tra dọn mỗi 1 giờ (dữ liệu thực tế có thể tồn tại khoảng 2–4 giờ). Chạy lại `bash deploy.sh` để redeploy đúng bản; **không xóa Docker volumes**.

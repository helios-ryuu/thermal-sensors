# Runbook: Giám sát nhiệt độ iMac (100% Docker Compose)

Tài liệu hướng dẫn vận hành, kiểm thử, bảo trì và xử lý sự cố cho stack giám sát nhiệt độ iMac.

---

## 1. Cấu trúc và Kiến trúc hệ thống

Toàn bộ hệ thống chạy bằng Docker Compose mà không phụ thuộc vào bất kỳ service systemd nào trên host. Dữ liệu runtime được lưu tập trung tại `~/.sens/`.

```text
Host Hardware (/sys, /etc/mbpfan.conf)
       │ (read-only bind-mount)
       ▼
┌────────────────────────────────────────────────────────┐
│ Docker Compose (thermal-monitoring)                    │
│                                                        │
│  ┌──────────────────┐                                  │
│  │ thermal-collector│ (Chạy liên tục mỗi 10s)          │
│  └────────┬─────────┘                                  │
│           │ ghi ~/.sens/textfile/thermal_sensors.prom  │
│           ▼                                            │
│  ┌──────────────────┐      ┌────────────────────────┐  │
│  │  node-exporter   ├─────►│   thermal-prometheus   │  │
│  └──────────────────┘      └───────────┬────────────┘  │
│                                        │               │
│                                        ▼               │
│                            ┌────────────────────────┐  │
│                            │    thermal-grafana     │  │
│                            └────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

### Các tệp trong dự án:
| Tệp / Thư mục | Vai trò |
| --- | --- |
| `compose.yaml` | Khai báo 4 container, network, secrets và volumes |
| `collector/Dockerfile` | Image collector (Alpine + Python 3 + `lm-sensors`) |
| `collector/collect_sensors.py` | Script thu thập cảm biến và cấu hình `mbpfan` |
| `prometheus/prometheus.yml` | Cấu hình scrape node-exporter mỗi 10s |
| `grafana/provisioning/` | Tự động cấu hình Prometheus datasource và dashboard provider |
| `grafana/dashboards/imac-thermal.json` | Dashboard giám sát nhiệt độ iMac |
| `.env.example` | Mẫu biến môi trường cấu hình IP, port, thư mục lưu trữ |
| `scripts/setup.sh` | Script 1-click khởi tạo thư mục `~/.sens`, secret và chạy stack |
| `scripts/clean.sh` | Script dừng stack hoặc xóa sạch dữ liệu (`--purge`) |
| `scripts/cleanup-legacy.sh` | Script dọn dẹp các tệp/service của phiên bản cũ trên host |

---

## 2. Dọn dẹp phiên bản cũ (nếu có)

Nếu hệ thống đã từng triển khai phiên bản cũ (dùng `/opt/thermal-monitoring` và systemd), chạy script sau:

```bash
./scripts/cleanup-legacy.sh
```

---

## 3. Triển khai 1-Click

Từ thư mục mã nguồn của dự án:

```bash
./scripts/setup.sh
```

Script sẽ thực hiện các bước:
1. Tạo file `.env` từ `.env.example` nếu chưa có.
2. Tạo thư mục `~/.sens/{textfile,prometheus,grafana,secrets}` với quyền ghi phù hợp.
3. Sinh mật khẩu ngẫu nhiên cho admin Grafana tại `~/.sens/secrets/grafana_admin_password.txt`.
4. Build image collector và chạy `docker compose up -d`.

---

## 4. Nghiệm thu & Kiểm tra

### 4.1. Kiểm tra trạng thái Container
```bash
docker compose ps
```
Cả 4 container (`thermal-collector`, `thermal-node-exporter`, `thermal-prometheus`, `thermal-grafana`) phải ở trạng thái `Up`.

### 4.2. Kiểm tra Collector Log & Metric
```bash
# Xem log collector
docker compose logs --tail 20 collector

# Kiểm tra file metric trong ~/.sens
cat ~/.sens/textfile/thermal_sensors.prom

# Kiểm tra metric qua Prometheus API
curl -fsSG --data-urlencode 'query=thermal_collector_success' http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_temperature_celsius' http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
```

### 4.3. Truy cập Grafana
- Mở trình duyệt: `http://100.120.64.5:3000` (hoặc IP cấu hình trong `.env`).
- Đăng nhập với User: `admin`, Password: [Được in trong lần chạy `setup.sh` đầu tiên hoặc xem trong `~/.sens/secrets/grafana_admin_password.txt`].
- Vào dashboard **Cảm biến nhiệt độ iMac**.

---

## 5. Vận hành & Bảo trì

### 5.1. Xem logs
```bash
docker compose logs -f [collector|prometheus|grafana|node-exporter]
```

### 5.2. Khởi động lại hoặc cập nhật
```bash
# Khởi động lại
docker compose restart

# Cập nhật code / rebuild collector
git pull
docker compose build collector
docker compose up -d
```

### 5.3. Dừng hệ thống
```bash
./scripts/clean.sh
```

### 5.4. Xóa sạch và khởi tạo lại từ đầu (Purge / Reset)
```bash
./scripts/clean.sh --purge
./scripts/setup.sh
```

---

## 6. Xử lý sự cố (Troubleshooting)

| Hiện tượng | Nguyên nhân có thể | Cách xử lý |
| --- | --- | --- |
| `thermal_collector_success` trả về `0` | Container collector không đọc được `/sys` | Kiểm tra volume mount `/sys:/sys:ro` trong `compose.yaml` |
| Grafana báo Permission Denied khi ghi db | Quyền thư mục `~/.sens/grafana` bị giới hạn | Chạy `chmod 777 ~/.sens/grafana` |
| Prometheus không ghi được TSDB | Quyền thư mục `~/.sens/prometheus` bị giới hạn | Chạy `chmod 777 ~/.sens/prometheus` |
| Grafana không truy cập được qua Tailscale | IP Tailscale thay đổi hoặc chưa bật tailscaled | Kiểm tra `tailscale ip -4` và cập nhật `GRAFANA_BIND_IP` trong `.env` |

# Runbook: Giám sát phần cứng và hệ thống iMac (100% Docker Compose & Tailscale)

Tài liệu hướng dẫn vận hành, kiểm thử, bảo trì và xử lý sự cố cho stack giám sát nhiệt độ và tài nguyên hệ thống iMac qua mạng Tailscale.

---

## 1. Cấu trúc và Kiến trúc hệ thống

Toàn bộ hệ thống chạy bằng Docker Compose mà không phụ thuộc vào bất kỳ service systemd nào trên host. Dữ liệu runtime được lưu tập trung tại `~/.sens/`.

```text
Host Hardware (/proc, /sys, /, /etc/mbpfan.conf)
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
│  │(Metric OS + Text)│      └───────────┬────────────┘  │
│  └──────────────────┘                  │               │
│                                        ▼               │
│                            ┌────────────────────────┐  │
│                            │    thermal-grafana     │  │
│                            └───────────┬────────────┘  │
└────────────────────────────────────────┼───────────────┘
                                         ▼
                               Tailscale Network
                               (<TAILSCALE_IP>:3000)
```

### Các tệp trong dự án:
| Tệp / Thư mục | Vai trò |
| --- | --- |
| `compose.yaml` | Khai báo 4 container (collector, node-exporter, prometheus, grafana) |
| `collector/Dockerfile` | Image collector (Alpine + Python 3 + `lm-sensors`) |
| `collector/collect_sensors.py` | Script thu thập cảm biến phần cứng (kèm TA0V ~room temp) và `mbpfan` |
| `prometheus/prometheus.yml` | Cấu hình scrape node-exporter mỗi 10s |
| `grafana/provisioning/` | Tự động nạp Prometheus datasource và Dashboards |
| `grafana/dashboards/imac-thermal.json` | Dashboard 1: Cảm biến nhiệt độ iMac |
| `grafana/dashboards/system-resources.json` | Dashboard 2: Tài nguyên hệ thống (CPU, RAM, Disk, Net) |
| `grafana/dashboards/system-health.json` | Dashboard 3: Sức khỏe hệ thống (TCP, Uptime, Inode, FS) |
| `.env.example` | Mẫu biến môi trường cấu hình IP, port, credentials |
| `scripts/setup.sh` | Script 1-click khởi tạo thư mục `~/.sens` và chạy stack |
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

1. Chuẩn bị file `.env`:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Chỉnh sửa các giá trị `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD` nếu cần.

2. Chạy setup:
   ```bash
   ./scripts/setup.sh
   ```

Script sẽ tự động:
1. Tạo thư mục `~/.sens/{textfile,prometheus,grafana}` với quyền ghi phù hợp.
2. Build image collector và chạy toàn bộ 4 container trong Docker Compose.

---

## 4. Nghiệm thu & Kiểm tra

### 4.1. Kiểm tra trạng thái Container
```bash
docker compose ps
```
Cả 4 container (`thermal-collector`, `thermal-node-exporter`, `thermal-prometheus`, `thermal-grafana`) phải ở trạng thái `Up`.

### 4.2. Kiểm tra Collector Log
```bash
# Xem log collector
docker compose logs --tail 20 collector
```

### 4.3. Truy cập Grafana
- **Qua Mạng Tailscale**: Mở `http://<TAILSCALE_IP>:3000` (hoặc IP Tailscale cấu hình trong `.env`).
- Đăng nhập với User và Password đã thiết lập trong file `.env`.
- Mở thư mục **Giám sát hệ thống** để xem 3 dashboards:
  1. *Cảm biến nhiệt độ iMac*
  2. *Tài nguyên hệ thống*
  3. *Sức khỏe hệ thống*

---

## 5. Vận hành & Bảo trì

### 5.1. Xem logs
```bash
docker compose logs -f [collector|prometheus|grafana|node-exporter]
```

### 5.2. Khởi động lại hoặc cập nhật
```bash
docker compose restart
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

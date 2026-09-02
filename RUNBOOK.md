# Runbook: Giám sát nhiệt độ iMac (100% Docker Compose & Cloudflare Tunnel)

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
│                            │    thermal-grafana     │◄─┼──┐
│                            └────────────────────────┘  │  │
│                                                        │  │
│  ┌────────────────────┐                                │  │
│  │ cloudflared_tunnel │────────────────────────────────┘  │
│  └────────┬───────────┘ (Cloudflare Tunnel)               │
└───────────┼───────────────────────────────────────────────┘
            ▼
    Internet / Zero Trust
```

### Các tệp trong dự án:
| Tệp / Thư mục | Vai trò |
| --- | --- |
| `compose.yaml` | Khai báo 5 container (collector, node-exporter, prometheus, grafana, cloudflared) |
| `collector/Dockerfile` | Image collector (Alpine + Python 3 + `lm-sensors`) |
| `collector/collect_sensors.py` | Script thu thập cảm biến và cấu hình `mbpfan` |
| `prometheus/prometheus.yml` | Cấu hình scrape node-exporter mỗi 10s |
| `grafana/provisioning/` | Tự động cấu hình Prometheus datasource và dashboard provider |
| `grafana/dashboards/imac-thermal.json` | Dashboard giám sát nhiệt độ iMac |
| `.env.example` | Mẫu biến môi trường cấu hình IP, port, credentials, Cloudflare Tunnel token |
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
   ```
   Chỉnh sửa các giá trị `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`, và `TUNNEL_TOKEN`.

2. Chạy setup:
   ```bash
   ./scripts/setup.sh
   ```

Script sẽ tự động:
1. Tạo thư mục `~/.sens/{textfile,prometheus,grafana}` với quyền ghi phù hợp.
2. Build image collector và chạy toàn bộ 5 container trong Docker Compose.

---

## 4. Nghiệm thu & Kiểm tra

### 4.1. Kiểm tra trạng thái Container
```bash
docker compose ps
```
Cả 5 container (`thermal-collector`, `thermal-node-exporter`, `thermal-prometheus`, `thermal-grafana`, `cloudflared_tunnel`) phải ở trạng thái `Up`.

### 4.2. Kiểm tra Collector Log & Cloudflare Tunnel
```bash
# Xem log collector
docker compose logs --tail 20 collector

# Xem log Cloudflare Tunnel
docker compose logs --tail 20 cloudflared
```

### 4.3. Truy cập Grafana
- **Qua Cloudflare Tunnel**: Truy cập domain/hostname đã gán trên Cloudflare Zero Trust (trỏ tới Service `http://grafana:3000` hoặc `http://thermal-grafana:3000`).
- **Qua Mạng Tailscale / Cục bộ**: Mở `http://100.120.64.5:3000` (hoặc IP cấu hình trong `.env`).
- Đăng nhập với User và Password đã thiết lập trong file `.env`.

---

## 5. Vận hành & Bảo trì

### 5.1. Xem logs
```bash
docker compose logs -f [collector|prometheus|grafana|cloudflared]
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

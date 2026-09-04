# Runbook: Giám sát phần cứng, hệ thống & an ninh mạng iMac (100% Docker Compose & Tailscale)

Tài liệu hướng dẫn vận hành, kiểm thử, bảo trì và xử lý sự cố cho stack giám sát nhiệt độ, tài nguyên hệ thống và an ninh mạng iMac qua Tailscale.

---

## 1. Cấu trúc và Kiến trúc hệ thống

Toàn bộ hệ thống chạy bằng Docker Compose mà không phụ thuộc vào bất kỳ service systemd nào trên host. Dữ liệu runtime được lưu tập trung tại `~/.sens/`.

```text
Host Network & Hardware (/proc, /sys, /var/run/tailscale/tailscaled.sock, iptables)
       │ (network_mode: host, read-only bind-mounts)
       ▼
┌────────────────────────────────────────────────────────┐
│ Docker Compose Network (thermal-monitoring)            │
│                                                        │
│  ┌──────────────────────┐  ┌──────────────────────┐   │
│  │  thermal-collector   │  │    net-collector     │   │
│  │(Sensors & mbpfan 10s)│  │(Network & Sec 15s/2m)│   │
│  └──────────┬───────────┘  └──────────┬───────────┘   │
│             │                         │               │
│             ▼                         ▼               │
│      thermal_sensors.prom      network_metrics.prom   │
│             │                         │               │
│             └───────────┬─────────────┘               │
│                         ▼ (Thư mục ~/.sens/textfile/) │
│             ┌───────────────────────┐                 │
│             │     node-exporter     │                 │
│             └───────────┬───────────┘                 │
│                         ▼                             │
│             ┌───────────────────────┐                 │
│             │  thermal-prometheus   │                 │
│             └───────────┬────────────┘                │
│                         ▼                             │
│             ┌───────────────────────┐                 │
│             │    thermal-grafana    │                 │
│             │     (4 Dashboards)    │                 │
│             └───────────┬───────────┘                 │
└─────────────────────────┼─────────────────────────────┘
                          ▼
                  Tailscale Network
                  (<TAILSCALE_IP>:3000)
```

### Các tệp trong dự án:
| Tệp / Thư mục | Vai trò |
| --- | --- |
| `compose.yaml` | Khai báo 5 container (collector, net-collector, node-exporter, prometheus, grafana) |
| `collector/Dockerfile` | Image thermal collector (Alpine + Python 3 + `lm-sensors`) |
| `collector/collect_sensors.py` | Script thu thập cảm biến phần cứng (kèm TA0V động học) và `mbpfan` |
| `net-collector/Dockerfile` | Image network collector (Alpine + Python 3 + `iproute2` + `iptables` + `traceroute`) |
| `net-collector/collect_network.py` | Script thu thập NAT (CGNAT, Symmetric, Double NAT), Topology, Devices, Ports, iptables |
| `prometheus/prometheus.yml` | Cấu hình scrape node-exporter mỗi 10s |
| `grafana/provisioning/` | Tự động nạp Prometheus datasource và Dashboards |
| `grafana/dashboards/imac-thermal.json` | Dashboard 1: Cảm biến nhiệt độ iMac |
| `grafana/dashboards/system-resources.json` | Dashboard 2: Tài nguyên hệ thống (CPU, RAM, Disk, Net) |
| `grafana/dashboards/system-health.json` | Dashboard 3: Sức khỏe hệ thống (TCP, Uptime, Inode, FS) |
| `grafana/dashboards/network-security.json` | Dashboard 4: Mạng & Bảo mật (NAT, Devices, Ports, iptables) |
| `.env.example` | Mẫu biến môi trường cấu hình IP, port, credentials |
| `scripts/setup.sh` | Script 1-click khởi tạo thư mục `~/.sens` và chạy stack |
| `scripts/info.sh` | Script in thông tin kết nối và metadata dữ liệu (Cảm biến + Mạng) |
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
2. Build image cả 2 collectors (`collector` và `net-collector`) và khởi động cả 5 container trong Docker Compose.

---

## 4. Nghiệm thu & Kiểm tra

### 4.1. Kiểm tra trạng thái Container
```bash
docker compose ps
```
Cả 5 container (`thermal-collector`, `thermal-net-collector`, `thermal-node-exporter`, `thermal-prometheus`, `thermal-grafana`) phải ở trạng thái `Up`.

### 4.2. Kiểm tra Collector Logs
```bash
# Xem log thermal collector
docker compose logs --tail 20 collector

# Xem log network collector
docker compose logs --tail 20 net-collector
```

### 4.3. Truy cập Grafana
- **Qua Mạng Tailscale**: Mở `http://<TAILSCALE_IP>:3000` (hoặc IP Tailscale cấu hình trong `.env`).
- Đăng nhập với User và Password đã thiết lập trong file `.env`.
- Mở thư mục **Giám sát hệ thống** để xem **4 dashboards**:
  1. *Cảm biến nhiệt độ iMac*
  2. *Tài nguyên hệ thống*
  3. *Sức khỏe hệ thống*
  4. *Mạng & Bảo mật*

---

## 5. Vận hành & Bảo trì

### 5.1. Xem logs
```bash
docker compose logs -f [collector|net-collector|prometheus|grafana|node-exporter]
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

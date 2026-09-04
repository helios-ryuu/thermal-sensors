# Giám sát phần cứng, hệ thống & an ninh mạng iMac bằng Grafana & Docker Compose

Stack giám sát toàn diện, nhẹ và đóng gói **100% bằng Docker Compose** cho máy Ubuntu chạy trên iMac. Hiển thị lịch sử nhiệt độ (CPU, GPU, PCH, NVMe, quạt Apple SMC, nhiệt độ phòng ~TA0V), toàn bộ tài nguyên hệ thống (CPU, RAM, Disk I/O, Network, TCP), an ninh mạng & tường lửa và truy cập an toàn qua Tailscale.

Stack đi kèm **4 Dashboards Grafana** được nạp sẵn:
1. **Cảm biến nhiệt độ iMac (`imac-thermal`)**: Chi tiết nhiệt độ các linh kiện, nhiệt độ phòng động học (`TA0V` bù trừ RPM và nhiệt CPU/GPU), tốc độ quạt, công suất GPU và cấu hình `mbpfan`.
2. **Tài nguyên hệ thống (`system-resources`)**: Tải CPU (chi tiết theo mode), Load Average, phân bổ RAM, Disk I/O & IOPS, băng thông mạng & lỗi/drop gói.
3. **Sức khỏe hệ thống (`system-health`)**: Uptime, số nhân CPU, tổng dung lượng RAM, phiên bản Kernel, trạng thái kết nối TCP (ESTABLISHED, TIME_WAIT...), TCP Retransmissions, Inode usage và bảng dung lượng Filesystem.
4. **Mạng & Bảo mật (`network-security`)**: Giám sát CGNAT, Symmetric NAT, Double NAT, Topology các card mạng, kho thiết bị hợp nhất (LAN + Tailscale), các cổng mở lắng nghe, quy tắc tường lửa `iptables` và thiết lập bảo mật kernel.

---

## 🏗️ Kiến trúc hệ thống

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

* **Dữ liệu tập trung (`~/.sens/`)**:
  * `~/.sens/textfile/`: Chứa file metric trung gian `thermal_sensors.prom` và `network_metrics.prom`.
  * `~/.sens/prometheus/`: Cơ sở dữ liệu time-series TSDB của Prometheus.
  * `~/.sens/grafana/`: Dữ liệu SQLite và cấu hình của Grafana.

---

## ⚡ Bắt đầu nhanh (1-Click)

### 1. Dọn dẹp bản cũ trên host (nếu đã từng cài phiên bản cũ)
```bash
./scripts/cleanup-legacy.sh
```

### 2. Cấu hình & Khởi tạo stack (1-Click)
1. Tạo file `.env` từ `.env.example` và điền `GRAFANA_ADMIN_PASSWORD`:
   ```bash
   cp .env.example .env
   nano .env
   ```
2. Chạy setup:
   ```bash
   ./scripts/setup.sh
   ```
* Dashboard Grafana (qua Tailscale): `http://<TAILSCALE_IP>:3000` (hoặc IP cấu hình trong `.env`).
* Prometheus (nội bộ host): `http://127.0.0.1:9090`.

---

## ⚙️ Cấu hình tùy chỉnh (.env)

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SENS_DATA_DIR` | `/home/user/.sens` | Thư mục lưu trữ tập trung dữ liệu |
| `GRAFANA_ADMIN_USER` | `admin` | Tên đăng nhập Admin Grafana |
| `GRAFANA_ADMIN_PASSWORD` | `...` | Mật khẩu Admin Grafana (cấu hình trong `.env`) |
| `GRAFANA_BIND_IP` | `<TAILSCALE_IP>` | IP publish của Grafana (Địa chỉ IP Tailscale của máy hoặc 127.0.0.1) |
| `GRAFANA_PORT` | `3000` | Port truy cập Grafana |
| `PROMETHEUS_BIND_IP`| `127.0.0.1` | IP publish của Prometheus |
| `PROMETHEUS_PORT` | `9090` | Port truy cập Prometheus |
| `COLLECTOR_INTERVAL`| `10` | Chu kỳ thu thập cảm biến của thermal collector (giây) |
| `NET_COLLECTOR_FAST_INTERVAL`| `15` | Chu kỳ thu thập nhanh của net-collector (giây) |
| `NET_COLLECTOR_SLOW_INTERVAL`| `120` | Chu kỳ kiểm tra NAT/STUN/Traceroute của net-collector (giây) |
| `PROMETHEUS_RETENTION_TIME` | `15d` | Thời gian lưu trữ dữ liệu metric |
| `PROMETHEUS_RETENTION_SIZE` | `2GB` | Dung lượng tối đa của Prometheus TSDB |

---

## 🛠️ Quản lý & Dọn dẹp

* **Xem thông tin truy cập & metadata dữ liệu (Cảm biến + Mạng + Dung lượng):**
  ```bash
  ./scripts/info.sh
  ```

* **Xem trạng thái container / log:**
  ```bash
  docker compose ps
  docker compose logs -f net-collector
  ```

* **Dừng stack:**
  ```bash
  ./scripts/clean.sh
  ```

* **Dừng stack và xóa sạch toàn bộ dữ liệu (`~/.sens`):**
  ```bash
  ./scripts/clean.sh --purge
  ```

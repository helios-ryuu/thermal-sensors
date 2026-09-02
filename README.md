# Giám sát nhiệt độ iMac bằng Grafana và Docker Compose (100% Containerized)

Stack giám sát nhẹ cho Ubuntu trên iMac, hiển thị lịch sử nhiệt độ CPU, GPU, PCH, NVMe và tốc độ quạt từ `lm-sensors` trên Grafana, tích hợp Cloudflare Tunnel để truy cập an toàn từ xa.

Hệ thống được đóng gói **100% bằng Docker Compose**, không cần cài đặt các service hay timer systemd phức tạp trên host, dữ liệu được lưu trữ tập trung tại `~/.sens` (hoặc thư mục tùy chỉnh qua `.env`).

---

## 🏗️ Kiến trúc hệ thống

```text
Host Hardware (/sys, /etc/mbpfan.conf)
       │ (read-only bind-mount)
       ▼
┌────────────────────────────────────────────────────────┐
│ Docker Compose Network (thermal-monitoring)            │
│                                                        │
│  ┌──────────────────┐                                  │
│  │ thermal-collector│ (Chạy mỗi 10 giây)               │
│  └────────┬─────────┘                                  │
│           │ ghi metric textfile                        │
│           ▼                                            │
│  ┌──────────────────┐      ┌────────────────────────┐  │
│  │  node-exporter   ├─────►│       prometheus       │  │
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

* **Dữ liệu tập trung (`~/.sens/`)**:
  * `~/.sens/textfile/`: Chứa file metric trung gian `thermal_sensors.prom`.
  * `~/.sens/prometheus/`: Cơ sở dữ liệu time-series TSDB của Prometheus.
  * `~/.sens/grafana/`: Dữ liệu SQLite và cấu hình của Grafana.

---

## ⚡ Bắt đầu nhanh (1-Click)

### 1. Dọn dẹp bản cũ trên host (nếu đã từng cài phiên bản cũ)
```bash
./scripts/cleanup-legacy.sh
```

### 2. Cấu hình & Khởi tạo stack (1-Click)
1. Tạo file `.env` từ `.env.example` và điền `TUNNEL_TOKEN`, `GRAFANA_ADMIN_PASSWORD`:
   ```bash
   cp .env.example .env
   nano .env
   ```
2. Chạy setup:
   ```bash
   ./scripts/setup.sh
   ```
* Dashboard Grafana Cục bộ/Tailscale: `http://<GRAFANA_BIND_IP>:<GRAFANA_PORT>` (hoặc IP cấu hình trong `.env`).
* Dashboard Grafana Cloudflare: Tự động kết nối qua Cloudflare Tunnel (`cloudflared_tunnel`).
* Prometheus: `http://127.0.0.1:9090`.

---

## ⚙️ Cấu hình tùy chỉnh (.env)

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SENS_DATA_DIR` | `/home/user/.sens` | Thư mục lưu trữ tập trung dữ liệu |
| `GRAFANA_ADMIN_USER` | `admin` | Tên đăng nhập Admin Grafana |
| `GRAFANA_ADMIN_PASSWORD` | `...` | Mật khẩu Admin Grafana (cấu hình trong `.env`) |
| `TUNNEL_TOKEN` | `...` | Token Cloudflare Tunnel (lấy từ Cloudflare Zero Trust) |
| `GRAFANA_BIND_IP` | `<GRAFANA_BIND_IP>` | IP publish của Grafana (Tailscale IP hoặc 127.0.0.1) |
| `GRAFANA_PORT` | `<GRAFANA_PORT>` | Port truy cập Grafana |
| `PROMETHEUS_BIND_IP`| `127.0.0.1` | IP publish của Prometheus |
| `PROMETHEUS_PORT` | `9090` | Port truy cập Prometheus |
| `COLLECTOR_INTERVAL`| `10` | Chu kỳ thu thập cảm biến của collector (giây) |
| `PROMETHEUS_RETENTION_TIME` | `15d` | Thời gian lưu trữ dữ liệu metric |
| `PROMETHEUS_RETENTION_SIZE` | `2GB` | Dung lượng tối đa của Prometheus TSDB |

---

## 🛠️ Quản lý & Dọn dẹp

* **Xem trạng thái / log:**
  ```bash
  docker compose ps
  docker compose logs -f cloudflared
  ```

* **Dừng stack:**
  ```bash
  ./scripts/clean.sh
  ```

* **Dừng stack và xóa sạch toàn bộ dữ liệu (`~/.sens`):**
  ```bash
  ./scripts/clean.sh --purge
  ```

# Giám sát nhiệt độ iMac bằng Grafana và Docker Compose (100% Containerized)

Stack giám sát nhẹ cho Ubuntu trên iMac, hiển thị lịch sử nhiệt độ CPU, GPU, PCH, NVMe và tốc độ quạt từ `lm-sensors` trên Grafana.

Hệ thống được đóng gói **100% bằng Docker Compose**, không cần cài đặt các service hay timer systemd phức tạp trên host, dữ liệu được lưu trữ tập trung tại `~/.sens` (hoặc thư mục tùy chỉnh qua `.env`).

---

## 🏗️ Kiến trúc hệ thống

```text
Host Hardware (/sys, /etc/mbpfan.conf)
       │ (read-only bind-mount)
       ▼
┌────────────────────────────────────────────────────────┐
│ Docker Compose Network                                 │
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
│                            │        grafana         │  │
│                            └────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

* **Dữ liệu tập trung (`~/.sens/`)**:
  * `~/.sens/textfile/`: Chứa file metric trung gian `thermal_sensors.prom`.
  * `~/.sens/prometheus/`: Cơ sở dữ liệu time-series TSDB của Prometheus.
  * `~/.sens/grafana/`: Dữ liệu SQLite và cấu hình của Grafana.
  * `~/.sens/secrets/`: Mật khẩu đăng nhập Admin Grafana.

---

## ⚡ Bắt đầu nhanh (1-Click)

### 1. Dọn dẹp bản cũ trên host (nếu đã từng cài phiên bản cũ)
```bash
./scripts/cleanup-legacy.sh
```

### 2. Khởi tạo và chạy stack (1-Click)
```bash
./scripts/setup.sh
```
* Script sẽ tự tạo file `.env`, chuẩn bị thư mục `~/.sens`, tự sinh mật khẩu admin Grafana và bật toàn bộ container.
* Dashboard Grafana: `http://100.120.64.5:3000` (hoặc IP Tailscale cấu hình trong `.env`).
* Prometheus: `http://127.0.0.1:9090`.

---

## ⚙️ Cấu hình tùy chỉnh (.env)

Tất cả cấu hình có thể tùy chỉnh dễ dàng qua file `.env`:

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SENS_DATA_DIR` | `/home/user/.sens` | Thư mục lưu trữ tập trung toàn bộ dữ liệu & bí mật |
| `GRAFANA_BIND_IP` | `100.120.64.5` | IP publish của Grafana (khuyến nghị Tailscale IP) |
| `GRAFANA_PORT` | `3000` | Port truy cập Grafana |
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
  docker compose logs -f collector
  ```

* **Dừng stack:**
  ```bash
  ./scripts/clean.sh
  ```

* **Dừng stack và xóa sạch toàn bộ dữ liệu (`~/.sens`):**
  ```bash
  ./scripts/clean.sh --purge
  ```

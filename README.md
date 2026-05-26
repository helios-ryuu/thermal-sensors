# Giám sát nhiệt độ iMac bằng Grafana và Docker Compose

Tài liệu này mô tả một stack giám sát nhẹ cho máy Ubuntu chạy trên iMac, dùng
để hiển thị nhiệt độ và tốc độ quạt từ `lm-sensors` trên Grafana. Giải pháp
không dùng Kubernetes và không cần thay đổi cụm K8s đang phục vụ đồ án.

Hướng dẫn triển khai đầy đủ nằm trong [RUNBOOK.md](./RUNBOOK.md).

## Mục tiêu

- Hiển thị lịch sử nhiệt độ CPU, GPU, PCH và NVMe.
- Hiển thị tốc độ quạt Apple SMC và công suất GPU.
- Chỉ mở Grafana trên mạng Tailscale của máy, tại
  `http://100.120.64.5:3000`.
- Giới hạn lưu trữ Prometheus ở `15d` và `2GB`.
- Giữ việc thu thập cảm biến độc lập với các workload Kubernetes hiện có.

## Kiến trúc

```text
Ubuntu host
  lm-sensors (`sensors -j`)
       |
       v
  Python collector + systemd timer (mỗi 15 giây)
       |
       v
  /var/lib/thermal-sensors/textfile/thermal_sensors.prom
       |
       | read-only bind mount
       v
Docker Compose network
  node_exporter  --->  Prometheus  --->  Grafana
  (không publish)     (127.0.0.1)      (100.120.64.5)
```

Host Ubuntu chạy duy nhất thành phần cần đọc phần cứng: một collector Python
gọi `sensors -j` và ghi metric theo định dạng Prometheus textfile. Ba dịch vụ
container chỉ đọc metric, lưu time series và vẽ dashboard.

`mbpfan` đang tiếp tục điều khiển tốc độ quạt trên host theo cấu hình hiện có.
Stack này chỉ quan sát cảm biến và tốc độ quạt, không ghi cấu hình hay điều
khiển quạt.

## Mô hình GitHub và triển khai

Repository này được lưu trên GitHub và clone về máy Ubuntu tại
`~/thermal-sensors`. Thư mục đó là nguồn có quản lý phiên bản; sau mỗi lần
`git pull`, các tệp cần chạy được cài từ repository sang
`/opt/thermal-monitoring`.

```text
GitHub repository
       |
       | git clone / git pull --ff-only
       v
~/thermal-sensors              (mã nguồn và cấu hình có version)
       |
       | install
       v
/opt/thermal-monitoring        (bản cấu hình đang chạy và secret cục bộ)
```

Mật khẩu Grafana, volume Prometheus/Grafana và tệp metric không được commit
lên GitHub. Việc tách thư mục chạy khỏi working tree cũng tránh để một lần
`git pull` thay đổi dịch vụ trước khi người vận hành kiểm tra và áp dụng.

## Metric mặc định

Collector xuất các metric ổn định sau:

| Metric | Nội dung |
| --- | --- |
| `thermal_temperature_celsius{component,sensor}` | Nhiệt độ CPU package/core, GPU edge, PCH và NVMe |
| `thermal_fan_speed_rpm{component,sensor}` | Tốc độ quạt `Main` của Apple SMC |
| `thermal_gpu_power_watts{component,sensor}` | Công suất `PPT` của AMD GPU |
| `thermal_collector_success` | `1` nếu lần đọc gần nhất thành công, `0` nếu lỗi |
| `thermal_collector_timestamp_seconds` | Thời điểm collector thử đọc dữ liệu gần nhất |
| `thermal_mbpfan_config_valid` | `1` nếu `/etc/mbpfan.conf` đọc được và có thứ tự giới hạn hợp lệ |
| `thermal_mbpfan_temperature_threshold_celsius{threshold}` | Các mức `low`, `high`, `max` của `mbpfan` |
| `thermal_mbpfan_fan_speed_limit_rpm{fan,limit}` | Giới hạn `min`/`max` RPM của quạt trong `mbpfan` |

Output Apple SMC trên máy hiện có nhiều key mang giá trị sentinel hoặc không
đáng tin, ví dụ `-127.0 C`, giá trị âm bất thường và các key chưa xác định ý
nghĩa. Dashboard mặc định không lấy nhiệt độ Apple SMC thô. Runbook có quy
trình thêm từng key vào allowlist sau khi xác minh trên máy thật.

## Phạm vi truy cập và lưu trữ

| Thành phần | Địa chỉ publish | Lý do |
| --- | --- | --- |
| Grafana | `100.120.64.5:3000` | Chỉ truy cập từ tailnet |
| Prometheus | `127.0.0.1:9090` | Kiểm tra cục bộ hoặc SSH tunnel |
| node_exporter | Không publish | Chỉ được Prometheus đọc trong Compose network |

Prometheus được cấu hình lưu dữ liệu tối đa `15d` và `2GB`. Grafana yêu cầu
đăng nhập, tắt đăng ký người dùng mới và dùng mật khẩu admin tạo trong lúc cài
đặt.

Collector đọc read-only `/etc/mbpfan.conf`; dashboard dùng metric cấu hình để
hiện các ngưỡng nhiệt và giới hạn quạt thực tế thay cho giá trị hard-code.
Panel nhiệt độ tổng chỉ dùng CPU package làm đại diện CPU, trong khi panel CPU
riêng vẫn hiện package và từng core. Đường `trung bình toàn máy` là trung bình
của CPU package, GPU/PCH/NVMe và các nhiệt độ SMC đã allowlist đang có dữ liệu.

Các truy vấn tổng hợp nhiệt độ tính được trên lịch sử sensor còn lưu trong
Prometheus. Metric cấu hình không được backfill, nên mốc `mbpfan` chỉ xuất
hiện kể từ lúc collector phiên bản mới bắt đầu ghi nhận cấu hình.

Lưu ý cần kiểm tra trên máy thật: output `sensors` đã cung cấp báo dải quạt
`1200-2850 RPM`, khác với giá trị ghi đè `1600-2950 RPM` trong `mbpfan.conf`.
Giải pháp giám sát không tự động thay đổi cấu hình quạt này.

## Các tệp trong dự án

| Tệp | Vai trò | Đích trên Ubuntu |
| --- | --- | --- |
| `compose.yaml` | Chạy ba container giám sát | `/opt/thermal-monitoring/compose.yaml` |
| `collector/collect_sensors.py` | Chuyển `sensors -j` thành metric | `/opt/thermal-monitoring/collector/collect_sensors.py` |
| `systemd/thermal-sensors.*` | Lập lịch collector mỗi 15 giây | `/etc/systemd/system/` |
| `prometheus/prometheus.yml` | Scrape node_exporter | `/opt/thermal-monitoring/prometheus/` |
| `grafana/provisioning/` | Tự tạo datasource/dashboard provider | `/opt/thermal-monitoring/grafana/provisioning/` |
| `grafana/dashboards/imac-thermal.json` | Dashboard được nạp sẵn | `/opt/thermal-monitoring/grafana/dashboards/` |

## Yêu cầu

- Ubuntu trên iMac đọc được dữ liệu bằng `sensors -j`.
- User dịch vụ `helios` đọc được `/etc/mbpfan.conf` để xuất metric cấu hình.
- Tài khoản triển khai là `helios`, có quyền `sudo`.
- Docker Engine và Docker Compose plugin đã được cài đặt.
- Tailscale đã hoạt động, và địa chỉ của máy là `100.120.64.5`.
- Các cổng `3000` và `9090` không bị dịch vụ quan trọng khác sử dụng.

## Nguyên tắc vận hành

- Stack này được khởi động bằng `docker compose`, không dùng `kubectl`, Helm
  hay manifest Kubernetes.
- Không đổi binding Prometheus/node_exporter sang LAN công cộng.
- Dừng triển khai nếu preflight phát hiện xung đột cổng, thiếu dung lượng đĩa
  hoặc tài nguyên của máy đang ảnh hưởng workload đồ án.
- Các image hiện dùng tag `latest`; chỉ chạy `docker compose pull` sau khi đã
  sao lưu và sẵn sàng nghiệm thu hoặc rollback theo runbook.

## Triển khai

Thực hiện lần lượt các mục trong [RUNBOOK.md](./RUNBOOK.md):

1. Clone repository GitHub về `~/thermal-sensors`, hoặc `git pull --ff-only`
   nếu đã triển khai trước đó.
2. Chạy kiểm tra preflight.
3. Cài các tệp đã version hóa sang `/opt/thermal-monitoring` và bật
   `systemd timer`.
4. Khởi động Docker Compose và kiểm thử metric/dashboard.
5. Ghi nhận mật khẩu, quy trình cập nhật, sao lưu và gỡ bỏ.

## Tham chiếu

- [Docker Engine trên Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Grafana Docker](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/)
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Prometheus storage](https://prometheus.io/docs/prometheus/latest/storage/)
- [node_exporter textfile collector](https://github.com/prometheus/node_exporter/blob/master/README.md)
- [Tailscale IP và địa chỉ truy cập](https://tailscale.com/kb/1033/ip-and-dns-addresses)

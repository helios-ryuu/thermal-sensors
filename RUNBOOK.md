# Runbook: triển khai dashboard nhiệt độ iMac

Runbook này cài một stack giám sát độc lập trên máy Ubuntu/iMac:

- Collector chạy trên host bằng `systemd`, đọc `sensors -j` mỗi 15 giây.
- `node_exporter`, Prometheus và Grafana chạy bằng Docker Compose.
- Grafana chỉ lắng nghe trên địa chỉ Tailscale `100.120.64.5:3000`.
- Prometheus chỉ lắng nghe cục bộ tại `127.0.0.1:9090`.
- `mbpfan` tiếp tục điều khiển quạt như hiện tại; stack này chỉ quan sát.
- Không dùng, chỉnh sửa hoặc khởi động lại bất kỳ tài nguyên Kubernetes nào.
- Repository được clone từ GitHub về `~/thermal-sensors`; bản chạy được cài
  riêng sang `/opt/thermal-monitoring`.

## 1. Các tệp được cài

Sau khi clone repository về Ubuntu tại `~/thermal-sensors`, các tệp được ánh
xạ sang bản chạy như sau:

| Tệp trong dự án | Đích cài đặt | Vai trò |
| --- | --- | --- |
| `compose.yaml` | `/opt/thermal-monitoring/compose.yaml` | Khai báo container, volume và cổng publish |
| `collector/collect_sensors.py` | `/opt/thermal-monitoring/collector/collect_sensors.py` | Chuyển dữ liệu `sensors -j` thành metric |
| `systemd/thermal-sensors.service` | `/etc/systemd/system/thermal-sensors.service` | Chạy collector một lần |
| `systemd/thermal-sensors.timer` | `/etc/systemd/system/thermal-sensors.timer` | Chạy collector mỗi 15 giây |
| `prometheus/prometheus.yml` | `/opt/thermal-monitoring/prometheus/prometheus.yml` | Cho Prometheus thu thập metric |
| `grafana/provisioning/datasources/prometheus.yml` | `/opt/thermal-monitoring/grafana/provisioning/datasources/prometheus.yml` | Tạo nguồn dữ liệu Grafana |
| `grafana/provisioning/dashboards/thermal.yml` | `/opt/thermal-monitoring/grafana/provisioning/dashboards/thermal.yml` | Nạp dashboard từ tệp |
| `grafana/dashboards/imac-thermal.json` | `/opt/thermal-monitoring/grafana/dashboards/imac-thermal.json` | Dashboard nhiệt độ có sẵn |

Ba image Docker được pin cố định theo phiên bản đã chọn ngày `2026-05-26`:

| Dịch vụ | Image |
| --- | --- |
| Grafana | `grafana/grafana:13.0.1-ubuntu` |
| Prometheus | `prom/prometheus:v3.11.3` |
| node_exporter | `prom/node-exporter:v1.11.1` |

`~/thermal-sensors` là bản có quản lý phiên bản từ GitHub.
`/opt/thermal-monitoring` là bản được Docker Compose và `systemd` sử dụng.
Bí mật, metric và volume runtime không nằm trong repository.

## 2. Clone hoặc cập nhật repository từ GitHub

Lần triển khai đầu tiên, thay URL ví dụ bằng URL repository GitHub thực tế rồi
clone đúng vào đường dẫn quy ước:

```bash
REPO_URL="https://github.com/<tài-khoản>/<repository>.git"
git clone "$REPO_URL" "$HOME/thermal-sensors"
REPO_DIR="$HOME/thermal-sensors"
git -C "$REPO_DIR" status --short --branch
git -C "$REPO_DIR" rev-parse --short HEAD
```

Nếu repository đã tồn tại trên máy Ubuntu, không clone lại. Đảm bảo không có
thay đổi cục bộ chưa commit rồi cập nhật bằng `git pull --ff-only`:

```bash
REPO_DIR="$HOME/thermal-sensors"
git -C "$REPO_DIR" status --short --branch
git -C "$REPO_DIR" pull --ff-only
git -C "$REPO_DIR" rev-parse --short HEAD
```

Nếu `git status` hiển thị thay đổi cục bộ, không chạy `git pull` hoặc các bước
cài đặt tiếp theo; xử lý các thay đổi đó trước. Không đặt mật khẩu, dữ liệu
metric hoặc bản sao volume vào repository.

## 3. Cấu hình `mbpfan` hiện tại và giới hạn phạm vi

Máy đang có `/etc/mbpfan.conf` với các giá trị vận hành sau:

```ini
[general]
min_fan1_speed = 1600
max_fan1_speed = 2950
low_temp = 38
high_temp = 48
max_temp = 56
polling_interval = 1
```

Dashboard sử dụng `48°C` và `56°C` làm mốc màu để quan sát phản ứng của
`mbpfan`, đồng thời hiển thị dải cấu hình quạt `1600-2950 RPM`. Đây là ngưỡng
chính sách làm mát, không phải ngưỡng hỏng phần cứng.

**Điểm cần xác minh:** output `sensors` đã cung cấp báo quạt `Main` có
`min = 1200 RPM` và `max = 2850 RPM`, khác với giá trị ghi đè trong
`mbpfan.conf`. Stack này không sửa chênh lệch đó. Trước khi thay đổi cấu hình
quạt trong tương lai, kiểm tra sysfs và log `mbpfan` riêng biệt:

```bash
sudo systemctl status mbpfan --no-pager
sudo cat /etc/mbpfan.conf
find /sys/devices/platform -path '*applesmc*' \
  \( -name 'fan*_min' -o -name 'fan*_max' \) \
  -print -exec cat {} \;
journalctl -u mbpfan -n 50 --no-pager
```

Không sao chép hay ghi đè `/etc/mbpfan.conf` trong các bước dưới đây.

## 4. Kiểm tra trước khi cài đặt

Đăng nhập máy Ubuntu bằng user `helios`, dùng working tree vừa clone/pull và
kiểm tra đủ tệp:

```bash
REPO_DIR="$HOME/thermal-sensors"
test "$(id -un)" = "helios"
test -d "$REPO_DIR/.git"
test -f "$REPO_DIR/compose.yaml"
test -f "$REPO_DIR/collector/collect_sensors.py"
test -f "$REPO_DIR/prometheus/prometheus.yml"
test -f "$REPO_DIR/grafana/dashboards/imac-thermal.json"
```

Xác nhận các chương trình đã có sẵn. Runbook không cài Docker hay Tailscale
mới lên máy đang chạy đồ án:

```bash
command -v git sensors python3 docker tailscale curl openssl
docker compose version
docker info --format 'Docker server: {{.ServerVersion}}'
```

Kiểm tra `lm-sensors` trả về JSON hợp lệ:

```bash
sensors -j >/tmp/thermal-sensors-preflight.json
python3 -m json.tool /tmp/thermal-sensors-preflight.json >/dev/null
rm -f /tmp/thermal-sensors-preflight.json
```

Xác nhận Tailscale IP. Chỉ tiếp tục nếu lệnh in ra dòng `OK`:

```bash
tailscale ip -4
test "$(tailscale ip -4 | head -n 1)" = "100.120.64.5" \
  && echo "OK: địa chỉ Tailscale đúng với compose.yaml" \
  || echo "DỪNG LẠI: cần cập nhật binding Grafana trước khi chạy stack"
```

Kiểm tra tài nguyên và cổng. Không dừng tiến trình nào chỉ để nhường cổng cho
stack này:

```bash
df -h / /var/lib/docker
free -h
sudo ss -ltnp | grep -E ':(3000|9090)\b' || true
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
```

Dừng triển khai nếu cổng `3000` hoặc `9090` đang được dùng, dung lượng đĩa
không đủ, hoặc việc bổ sung container có nguy cơ ảnh hưởng workload đồ án.

## 5. Cài các tệp ứng dụng

Tạo thư mục đích. Cấu hình dưới `/opt` thuộc user `helios`; chỉ thư mục
`systemd` cần cài qua `sudo`:

```bash
sudo install -d -m 0755 -o helios -g helios /opt/thermal-monitoring
install -d -m 0755 \
  /opt/thermal-monitoring/collector \
  /opt/thermal-monitoring/prometheus \
  /opt/thermal-monitoring/grafana/provisioning/datasources \
  /opt/thermal-monitoring/grafana/provisioning/dashboards \
  /opt/thermal-monitoring/grafana/dashboards
install -d -m 0700 /opt/thermal-monitoring/secrets
sudo install -d -m 0755 -o helios -g helios /var/lib/thermal-sensors/textfile
```

Cài cấu hình và collector từ repository đã checkout:

```bash
REPO_DIR="$HOME/thermal-sensors"
install -m 0644 "$REPO_DIR/compose.yaml" /opt/thermal-monitoring/compose.yaml
install -m 0755 "$REPO_DIR/collector/collect_sensors.py" \
  /opt/thermal-monitoring/collector/collect_sensors.py
install -m 0644 "$REPO_DIR/prometheus/prometheus.yml" \
  /opt/thermal-monitoring/prometheus/prometheus.yml
install -m 0644 "$REPO_DIR/grafana/provisioning/datasources/prometheus.yml" \
  /opt/thermal-monitoring/grafana/provisioning/datasources/prometheus.yml
install -m 0644 "$REPO_DIR/grafana/provisioning/dashboards/thermal.yml" \
  /opt/thermal-monitoring/grafana/provisioning/dashboards/thermal.yml
install -m 0644 "$REPO_DIR/grafana/dashboards/imac-thermal.json" \
  /opt/thermal-monitoring/grafana/dashboards/imac-thermal.json
```

## 6. Thử collector trước khi lập lịch

Collector xuất các metric sau:

| Metric | Nội dung |
| --- | --- |
| `thermal_temperature_celsius{component,sensor}` | Nhiệt độ CPU package/core, GPU edge, PCH và NVMe |
| `thermal_fan_speed_rpm{component,sensor}` | Tốc độ quạt Apple SMC `Main` |
| `thermal_gpu_power_watts{component,sensor}` | Công suất AMD GPU `PPT` |
| `thermal_collector_success` | Trạng thái lần thu thập gần nhất |
| `thermal_collector_timestamp_seconds` | Thời điểm lần thu thập gần nhất |

Chạy collector thủ công:

```bash
/usr/bin/python3 /opt/thermal-monitoring/collector/collect_sensors.py
cat /var/lib/thermal-sensors/textfile/thermal_sensors.prom
grep -E 'thermal_temperature_celsius.* (-|127)' \
  /var/lib/thermal-sensors/textfile/thermal_sensors.prom \
  && echo "DỪNG LẠI: kiểm tra giá trị nhiệt độ bất thường" \
  || echo "OK: không có nhiệt độ sentinel hoặc âm"
```

Tệp metric cần có `thermal_collector_success 1`. Collector mặc định chỉ lấy
CPU, GPU, PCH, NVMe và quạt chính; các nhiệt độ Apple SMC thô bị bỏ qua vì
output thực tế chứa nhiều giá trị như `-127.0°C` hoặc giá trị âm bất thường.

## 7. Bật lịch thu thập bằng `systemd`

Cài unit và timer từ repository:

```bash
REPO_DIR="$HOME/thermal-sensors"
sudo install -m 0644 "$REPO_DIR/systemd/thermal-sensors.service" \
  /etc/systemd/system/thermal-sensors.service
sudo install -m 0644 "$REPO_DIR/systemd/thermal-sensors.timer" \
  /etc/systemd/system/thermal-sensors.timer
sudo systemctl daemon-reload
sudo systemctl enable --now thermal-sensors.timer
sudo systemctl start thermal-sensors.service
systemctl status thermal-sensors.timer --no-pager
systemctl status thermal-sensors.service --no-pager
```

`thermal-sensors.service` là dịch vụ `oneshot`, nên trạng thái
`inactive (dead)` sau khi chạy thành công là bình thường. Timer phải ở trạng
thái `active (waiting)`. Khi cần xem lỗi:

```bash
journalctl -u thermal-sensors.service -n 50 --no-pager
```

## 8. Tạo bí mật đăng nhập Grafana

Xác nhận UID mà image Grafana đã pin sử dụng. Kết quả thông thường cần chứa
`uid=472`:

```bash
docker pull grafana/grafana:13.0.1-ubuntu
docker run --rm --entrypoint id grafana/grafana:13.0.1-ubuntu
```

Đặt UID vừa xác nhận vào biến `GRAFANA_UID`; nếu kết quả trên không phải
`472`, thay giá trị trước khi chạy:

```bash
GRAFANA_UID=472
umask 077
openssl rand -base64 32 > /tmp/grafana_admin_password.txt
echo "Lưu mật khẩu Grafana admin sau vào trình quản lý mật khẩu:"
cat /tmp/grafana_admin_password.txt
sudo install -o "$GRAFANA_UID" -g "$GRAFANA_UID" -m 0400 \
  /tmp/grafana_admin_password.txt \
  /opt/thermal-monitoring/secrets/grafana_admin_password.txt
rm -f /tmp/grafana_admin_password.txt
```

Tệp bí mật được tạo tại `/opt/thermal-monitoring/secrets`, nằm ngoài
`$REPO_DIR`. `.gitignore` cũng chặn `secrets/*.txt` nếu ai đó vô tình tạo
secret trong working tree; không commit mật khẩu lên GitHub.

## 9. Kiểm tra và khởi động Docker Compose

`compose.yaml` đã quy định:

| Dịch vụ | Truy cập từ host |
| --- | --- |
| Grafana | `100.120.64.5:3000` qua Tailscale |
| Prometheus | `127.0.0.1:9090` trên chính host |
| node_exporter | Không publish port; chỉ Prometheus truy cập qua network Docker |

Kiểm tra cấu hình đã cài trước khi tạo container:

```bash
cd /opt/thermal-monitoring
docker compose config --quiet
docker compose config --images
```

Danh sách image phải đúng ba tag được pin tại mục 1. Nếu đạt, khởi động stack:

```bash
docker compose pull
docker compose up -d
docker compose ps
sudo ss -ltnp | grep -E ':(3000|9090)\b'
```

Kết quả phải cho thấy Grafana chỉ bind vào `100.120.64.5:3000`, Prometheus
chỉ bind vào `127.0.0.1:9090`, và không có port host cho node_exporter.

## 10. Nghiệm thu

### 9.1. Collector và Prometheus

```bash
systemctl list-timers thermal-sensors.timer --no-pager
cat /var/lib/thermal-sensors/textfile/thermal_sensors.prom
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_collector_success' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_temperature_celsius' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
```

Tiêu chí đạt:

- Timer đang chờ lần chạy kế tiếp.
- Target `thermal-node-exporter` có trạng thái `up`.
- `thermal_collector_success` trả về `1`.
- Có nhiệt độ CPU/GPU/PCH/NVMe, tốc độ quạt và công suất GPU nếu phần cứng
  đang cung cấp dữ liệu tương ứng.
- Không có nhiệt độ âm hoặc sentinel `-127`.

### 9.2. Grafana qua Tailscale

Từ host Ubuntu:

```bash
curl -I http://100.120.64.5:3000/login
```

Từ một máy trong cùng tailnet, mở `http://100.120.64.5:3000`, đăng nhập bằng
user `admin` và mật khẩu đã lưu, sau đó mở thư mục **Giám sát nhiệt độ** và
dashboard **Cảm biến nhiệt độ iMac**.

Dashboard cần hiển thị trạng thái collector, tuổi dữ liệu, nhiệt độ, tốc độ
quạt và công suất GPU. Mốc màu nhiệt độ theo `mbpfan` phải xuất hiện ở
`48°C`/`56°C`.

Từ máy khác trong tailnet, Prometheus và node_exporter không được truy cập qua
địa chỉ Tailscale:

```bash
curl --connect-timeout 3 http://100.120.64.5:9090/-/ready
curl --connect-timeout 3 http://100.120.64.5:9100/metrics
```

Hai lệnh trên được kỳ vọng thất bại kết nối.

## 11. Thêm một nhiệt độ Apple SMC đã xác minh

Không xuất toàn bộ key Apple SMC vì máy hiện có nhiều slot vô hiệu. Chỉ thêm
một key sau khi theo dõi thấy nó hợp lệ:

```bash
watch -n 2 sensors
sensors -j | python3 -m json.tool
```

Sửa allowlist trong tệp có quản lý phiên bản
`$REPO_DIR/collector/collect_sensors.py`, đẩy thay đổi lên GitHub nếu chỉnh ở
máy phát triển, rồi pull về máy Ubuntu. Ví dụ nội dung thay đổi:

```python
APPLE_TEMPERATURE_ALLOWLIST = {
    "TC0P": "cpu_proximity",
}
```

Sau khi repository trên Ubuntu chứa thay đổi, cài lại collector rồi xác nhận
metric. Không chỉ sửa riêng bản trong `/opt`, vì lần triển khai sau sẽ ghi đè:

```bash
REPO_DIR="$HOME/thermal-sensors"
install -m 0755 "$REPO_DIR/collector/collect_sensors.py" \
  /opt/thermal-monitoring/collector/collect_sensors.py
/usr/bin/python3 /opt/thermal-monitoring/collector/collect_sensors.py
grep 'component="applesmc"' /var/lib/thermal-sensors/textfile/thermal_sensors.prom
```

Panel GPU/PCH/NVMe/SMC trong dashboard tự hiển thị sensor mới.

## 12. Vận hành thường ngày

Xem trạng thái và log:

```bash
cd /opt/thermal-monitoring
docker compose ps
docker compose logs --tail=100 prometheus grafana node-exporter
journalctl -u thermal-sensors.service -n 50 --no-pager
```

Khởi động lại các container giám sát, không tác động `mbpfan` hay Kubernetes:

```bash
cd /opt/thermal-monitoring
docker compose restart
docker compose ps
```

Dừng giao diện và lưu trữ metric nhưng giữ nguyên volume:

```bash
cd /opt/thermal-monitoring
docker compose down
```

Khởi động lại:

```bash
cd /opt/thermal-monitoring
docker compose up -d
```

## 13. Cập nhật cấu hình từ GitHub

Mỗi lần repository có thay đổi cần áp dụng lên máy Ubuntu, kiểm tra working
tree sạch, chạy `git pull --ff-only`, rồi cài lại các tệp version hóa. Thao
tác recreate bên dưới chỉ tác động ba container giám sát; thực hiện vào thời
điểm chấp nhận được gián đoạn ngắn của dashboard:

```bash
REPO_DIR="$HOME/thermal-sensors"
git -C "$REPO_DIR" status --short --branch
git -C "$REPO_DIR" pull --ff-only

install -m 0644 "$REPO_DIR/compose.yaml" /opt/thermal-monitoring/compose.yaml
install -m 0755 "$REPO_DIR/collector/collect_sensors.py" \
  /opt/thermal-monitoring/collector/collect_sensors.py
install -m 0644 "$REPO_DIR/prometheus/prometheus.yml" \
  /opt/thermal-monitoring/prometheus/prometheus.yml
install -m 0644 "$REPO_DIR/grafana/provisioning/datasources/prometheus.yml" \
  /opt/thermal-monitoring/grafana/provisioning/datasources/prometheus.yml
install -m 0644 "$REPO_DIR/grafana/provisioning/dashboards/thermal.yml" \
  /opt/thermal-monitoring/grafana/provisioning/dashboards/thermal.yml
install -m 0644 "$REPO_DIR/grafana/dashboards/imac-thermal.json" \
  /opt/thermal-monitoring/grafana/dashboards/imac-thermal.json
sudo install -m 0644 "$REPO_DIR/systemd/thermal-sensors.service" \
  /etc/systemd/system/thermal-sensors.service
sudo install -m 0644 "$REPO_DIR/systemd/thermal-sensors.timer" \
  /etc/systemd/system/thermal-sensors.timer

sudo systemctl daemon-reload
sudo systemctl restart thermal-sensors.timer
sudo systemctl start thermal-sensors.service
cd /opt/thermal-monitoring
docker compose config --quiet
docker compose pull
docker compose up -d --force-recreate
docker compose ps
```

Sau khi cập nhật, chạy lại các bước nghiệm thu tại mục 10. Secret và volume
không bị ghi đè bởi thao tác cài đặt này.

## 14. Sao lưu trước khi nâng cấp

Lệnh sau sao lưu cấu hình không chứa bí mật và dừng ngắn Prometheus/Grafana
để sao lưu volume nhất quán. Collector và `mbpfan` vẫn chạy bình thường.

```bash
BACKUP_DIR="$HOME/backups/thermal-monitoring/$(date +%F-%H%M%S)"
mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/configuration.tgz" \
  --exclude='secrets/grafana_admin_password.txt' \
  -C /opt thermal-monitoring

cd /opt/thermal-monitoring
docker compose stop prometheus grafana
docker run --rm \
  -v thermal-monitoring-prometheus-data:/data:ro \
  -v "$BACKUP_DIR":/backup \
  alpine:3.22 \
  tar -czf /backup/prometheus-data.tgz -C /data .
docker run --rm \
  -v thermal-monitoring-grafana-data:/data:ro \
  -v "$BACKUP_DIR":/backup \
  alpine:3.22 \
  tar -czf /backup/grafana-data.tgz -C /data .
docker compose start prometheus grafana
ls -lh "$BACKUP_DIR"
```

Mật khẩu Grafana phải được giữ trong trình quản lý mật khẩu, không nằm trong
archive cấu hình.

## 15. Nâng phiên bản có kiểm soát

Không đổi image sang tag `latest`. Trước khi nâng cấp:

1. Đọc ghi chú phát hành chính thức của Grafana, Prometheus và node_exporter.
2. Chạy sao lưu tại mục 14.
3. Cập nhật tag image trong `compose.yaml`, commit và push thay đổi lên
   GitHub.
4. Trên Ubuntu, pull repository và áp dụng theo mục 13.
5. Kiểm tra trạng thái dịch vụ:

```bash
cd /opt/thermal-monitoring
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:9090/-/ready
```

Lặp lại các kiểm thử tại mục 10 sau khi nâng cấp.

## 16. Gỡ bỏ

Dừng stack và collector nhưng giữ dữ liệu để có thể phục hồi:

```bash
cd /opt/thermal-monitoring
docker compose down
sudo systemctl disable --now thermal-sensors.timer
```

Để xóa hoàn toàn lịch sử của riêng stack này, chỉ chạy sau khi đã sao lưu hoặc
đã chấp nhận mất dữ liệu:

```bash
cd /opt/thermal-monitoring
docker compose down -v
sudo systemctl disable --now thermal-sensors.timer
sudo rm -f /etc/systemd/system/thermal-sensors.service
sudo rm -f /etc/systemd/system/thermal-sensors.timer
sudo systemctl daemon-reload
sudo rm -rf /var/lib/thermal-sensors/textfile
sudo rm -rf /opt/thermal-monitoring
```

Quy trình gỡ bỏ này không tác động `/etc/mbpfan.conf`, dịch vụ `mbpfan` hoặc
bất kỳ tài nguyên Kubernetes nào. Working tree `~/thermal-sensors` được giữ
lại để tiếp tục theo dõi mã nguồn; việc xóa clone GitHub là thao tác riêng.

## 17. Xử lý sự cố nhanh

| Hiện tượng | Kiểm tra | Hành động trong phạm vi stack |
| --- | --- | --- |
| Dashboard không có dữ liệu | `thermal_collector_success`, `journalctl -u thermal-sensors.service` | Sửa lỗi đọc `sensors` hoặc quyền thư mục textfile, rồi chạy lại collector |
| Target Prometheus down | `docker compose logs node-exporter prometheus` | Xác nhận file `.prom` và network Compose |
| Grafana không truy cập được từ tailnet | `tailscale ip -4`, `ss -ltnp`, log Grafana | Xác nhận IP binding và kết nối Tailscale |
| Nhiệt độ Apple SMC bất thường | `sensors` và file `.prom` | Xóa key khỏi allowlist, không xuất sensor thô chưa xác minh |
| RPM khác kỳ vọng `mbpfan` | sysfs, `/etc/mbpfan.conf`, log `mbpfan` | Điều tra dịch vụ quạt riêng, không chỉnh từ dashboard |
| Dữ liệu chiếm đĩa nhanh | Volume Docker và cờ retention | Xác nhận giới hạn `15d`/`2GB`, sao lưu trước khi can thiệp |

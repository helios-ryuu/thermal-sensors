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

Ba dịch vụ Docker sử dụng tag `latest` theo `compose.yaml` hiện tại:

| Dịch vụ | Image |
| --- | --- |
| Grafana | `grafana/grafana:latest` |
| Prometheus | `prom/prometheus:latest` |
| node_exporter | `prom/node-exporter:latest` |

Tag `latest` có thể trỏ sang image khác sau mỗi lần `docker compose pull`.
Vì vậy, các bước làm mới image ở mục 15 luôn đi kèm sao lưu, ghi nhận image
đang chạy và nghiệm thu lại.

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
high_temp = 46
max_temp = 60
polling_interval = 1
```

Collector đọc read-only năm giá trị này thành metric; dashboard dùng giá trị
đã thu thập để hiển thị mốc nhiệt và dải quạt thay cho số hard-code. Đây là
ngưỡng chính sách làm mát, không phải ngưỡng hỏng phần cứng. Metric cấu hình
chỉ bắt đầu có lịch sử sau khi collector mới được triển khai; không backfill
ngược lên dữ liệu Prometheus cũ.

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
test -r /etc/mbpfan.conf \
  && echo "OK: helios đọc được cấu hình mbpfan" \
  || echo "DỪNG LẠI: helios không đọc được /etc/mbpfan.conf"
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
| `thermal_mbpfan_config_valid` | Trạng thái đọc/kiểm tra cấu hình `mbpfan` |
| `thermal_mbpfan_temperature_threshold_celsius{threshold}` | Ngưỡng `low`, `high`, `max` từ `mbpfan.conf` |
| `thermal_mbpfan_fan_speed_limit_rpm{fan,limit}` | Giới hạn RPM `min`, `max` từ `mbpfan.conf` |

Chạy collector thủ công:

```bash
/usr/bin/python3 /opt/thermal-monitoring/collector/collect_sensors.py
cat /var/lib/thermal-sensors/textfile/thermal_sensors.prom
grep -E 'thermal_temperature_celsius.* (-|127)' \
  /var/lib/thermal-sensors/textfile/thermal_sensors.prom \
  && echo "DỪNG LẠI: kiểm tra giá trị nhiệt độ bất thường" \
  || echo "OK: không có nhiệt độ sentinel hoặc âm"
grep '^thermal_mbpfan_' /var/lib/thermal-sensors/textfile/thermal_sensors.prom
```

Tệp metric cần có `thermal_collector_success 1` và
`thermal_mbpfan_config_valid 1`. Nếu config không đọc được hoặc không hợp lệ,
collector vẫn giữ metric cảm biến nhưng chỉ xuất cờ config bằng `0`, không
xuất threshold/limit. Collector mặc định chỉ lấy CPU, GPU, PCH, NVMe và quạt
chính; các nhiệt độ Apple SMC thô bị bỏ qua vì output thực tế chứa nhiều giá
trị như `-127.0°C` hoặc giá trị âm bất thường.

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

`compose.yaml` hiện dùng `grafana/grafana:latest`. Trước lần khởi động đầu
tiên, tải đúng tag này và xác nhận danh tính tiến trình trong container:

```bash
docker pull grafana/grafana:latest
docker run --rm --entrypoint id grafana/grafana:latest
```

Tại thời điểm kiểm tra, image trả về:

```text
uid=472(grafana) gid=0(root) groups=0(root),0(root)
```

Không dùng `install -o 472`: trên host Ubuntu không có user tên `472`, nên
`install` sẽ báo `invalid user: '472'`. Không cần ánh xạ UID/GID của
container sang user trên host. Thư mục `/opt/thermal-monitoring/secrets` đã
được tạo với mode `0700` cho `helios`; đặt file bên trong ở mode `0444` để
Grafana đọc được sau khi Compose mount secret vào
`/run/secrets/grafana_admin_password`, trong khi user khác trên host không thể
đi qua thư mục cha.

Nếu một mật khẩu đã từng bị hiển thị trong chat, issue hoặc log chia sẻ, bỏ
mật khẩu đó và tạo mật khẩu mới ở bước dưới. Chỉ hiển thị mật khẩu mới trong
terminal riêng để lưu ngay vào trình quản lý mật khẩu; không dán output vào
tài liệu hoặc kênh trao đổi.

```bash
umask 077
openssl rand -base64 32 > /tmp/grafana_admin_password.txt
echo "Lưu mật khẩu Grafana admin mới vào trình quản lý mật khẩu; không chia sẻ output:"
cat /tmp/grafana_admin_password.txt
install -m 0444 \
  /tmp/grafana_admin_password.txt \
  /opt/thermal-monitoring/secrets/grafana_admin_password.txt
rm -f /tmp/grafana_admin_password.txt
stat -c '%U:%G %a %n' /opt/thermal-monitoring/secrets
stat -c '%U:%G %a %n' \
  /opt/thermal-monitoring/secrets/grafana_admin_password.txt
```

Kết quả `stat` phải cho thấy thư mục chỉ `helios` truy cập được và file secret
có thể đọc trong container:

```text
helios:helios 700 /opt/thermal-monitoring/secrets
helios:helios 444 /opt/thermal-monitoring/secrets/grafana_admin_password.txt
```

Tên group có thể khác nếu tài khoản `helios` dùng primary group khác; các mode
`700` và `444` mới là điều kiện cần xác nhận.

Tệp bí mật được tạo tại `/opt/thermal-monitoring/secrets`, nằm ngoài
`$REPO_DIR`. `.gitignore` cũng chặn `secrets/*.txt` nếu ai đó vô tình tạo
secret trong working tree; không commit mật khẩu lên GitHub.

Biến `GF_SECURITY_ADMIN_PASSWORD__FILE` trong `compose.yaml` chỉ đặt mật khẩu
admin khi Grafana khởi tạo cơ sở dữ liệu lần đầu. Nếu volume Grafana đã được
khởi tạo trước đó, thay file secret không tự đổi mật khẩu của tài khoản
`admin`; khi đó dùng mật khẩu hiện tại, reset admin bằng công cụ Grafana, hoặc
khởi tạo lại riêng volume Grafana theo mục 16.2 nếu chấp nhận mất các thay đổi
tạo thủ công trong giao diện.

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

Danh sách image phải gồm đúng ba tag `latest` tại mục 1. Lưu ý:
`docker compose pull` sẽ lấy image mà registry đang trỏ tới bằng tag `latest`
tại thời điểm chạy. Với lần cài đầu tiên, nếu cấu hình đạt, khởi động stack:

```bash
docker compose pull
docker compose up -d
docker compose ps
sudo ss -ltnp | grep -E ':(3000|9090)\b'
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  thermal-node-exporter thermal-prometheus thermal-grafana \
  | tee "$HOME/thermal-monitoring-images-ban-dau.txt"
```

Kết quả phải cho thấy Grafana chỉ bind vào `100.120.64.5:3000`, Prometheus
chỉ bind vào `127.0.0.1:9090`, và không có port host cho node_exporter. Tệp
`~/thermal-monitoring-images-ban-dau.txt` ghi lại image ID thực sự đã chạy,
vì tag `latest` không đủ để nhận diện lại một bản triển khai về sau.

## 10. Nghiệm thu

### 10.1. Collector và Prometheus

```bash
systemctl list-timers thermal-sensors.timer --no-pager
cat /var/lib/thermal-sensors/textfile/thermal_sensors.prom
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_collector_success' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_temperature_celsius' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
curl -fsSG --data-urlencode 'query=thermal_mbpfan_config_valid' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
curl -fsSG --data-urlencode \
  'query=avg(thermal_temperature_celsius{component!="cpu"} or thermal_temperature_celsius{component="cpu",sensor="package"})' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
```

Tiêu chí đạt:

- Timer đang chờ lần chạy kế tiếp.
- Target `thermal-node-exporter` có trạng thái `up`.
- `thermal_collector_success` trả về `1`.
- `thermal_mbpfan_config_valid` trả về `1`, kèm ba ngưỡng nhiệt và hai giới
  hạn RPM từ cấu hình.
- Có nhiệt độ CPU/GPU/PCH/NVMe, tốc độ quạt và công suất GPU nếu phần cứng
  đang cung cấp dữ liệu tương ứng.
- Không có nhiệt độ âm hoặc sentinel `-127`.

### 10.2. Grafana qua Tailscale

Từ host Ubuntu:

```bash
curl -I http://100.120.64.5:3000/login
```

Từ một máy trong cùng tailnet, mở `http://100.120.64.5:3000`, đăng nhập bằng
user `admin` và mật khẩu đã lưu, sau đó mở thư mục **Giám sát nhiệt độ** và
dashboard **Cảm biến nhiệt độ iMac**.

Dashboard cần hiển thị trạng thái collector, tuổi dữ liệu, nhiệt độ cao nhất,
nhiệt độ trung bình, cấu hình `mbpfan`, tốc độ quạt và công suất GPU. Panel
`Tất cả nhiệt độ hợp lệ` chỉ có một line CPU package, thêm line trung bình và
không có line cấu hình `mbpfan`; panel CPU riêng vẫn có package cùng từng core.
Gauge, panel cấu hình và line giới hạn quạt phải phản ánh giá trị trong
`/etc/mbpfan.conf`. Các biểu đồ time-series có vùng nền ban ngày
`06:00-18:00` và ban đêm `18:00-06:00` theo múi giờ trình duyệt.

Khi chọn khoảng thời gian cũ trước lần triển khai collector mới, line CPU
package và trung bình vẫn tính từ dữ liệu sensor đã lưu; line/panel cấu hình
để trống vì stack không ghi giả lịch sử `mbpfan`.

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
tree sạch, chạy `git pull --ff-only`, rồi cài lại các tệp version hóa. Quy
trình này áp dụng cấu hình mới nhưng chủ động không pull image `latest`; việc
làm mới image được thực hiện riêng tại mục 15 sau khi sao lưu. Thao tác
recreate bên dưới chỉ tác động ba container giám sát; thực hiện vào thời điểm
chấp nhận được gián đoạn ngắn của dashboard:

```bash
REPO_DIR="$HOME/thermal-sensors"
git -C "$REPO_DIR" status --short --branch
git -C "$REPO_DIR" pull --ff-only
test -r /etc/mbpfan.conf \
  && echo "OK: helios đọc được cấu hình mbpfan" \
  || { echo "DỪNG LẠI: helios không đọc được /etc/mbpfan.conf"; exit 1; }

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
docker compose up -d --pull never --force-recreate
docker compose ps
```

Sau khi cập nhật, chạy lại các bước nghiệm thu tại mục 10. Secret và volume
không bị ghi đè bởi thao tác cài đặt này, và image đang chạy không đổi nếu
image cũ vẫn còn trên host.

## 14. Sao lưu trước khi nâng cấp hoặc đặt lại dữ liệu

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

## 15. Làm mới image `latest` có kiểm soát

Vì `compose.yaml` sử dụng tag `latest`, lệnh `docker compose pull` có thể thay
đổi phiên bản Grafana, Prometheus hoặc node_exporter mà không có thay đổi nào
trong GitHub. Không chạy lệnh này tự động theo lịch trên máy đang phục vụ đồ
án.

Trước khi làm mới image:

1. Chạy sao lưu tại mục 14.
2. Ghi lại image ID đang chạy và gắn tag rollback cục bộ để có thể quay lại
   nếu image mới gây lỗi.
3. Pull các tag `latest`, khởi tạo lại container và chạy nghiệm thu mục 10.

```bash
cd /opt/thermal-monitoring
docker compose config --quiet

STAMP="$(date +%Y%m%d-%H%M%S)"
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  thermal-node-exporter thermal-prometheus thermal-grafana \
  | tee "$HOME/thermal-monitoring-images-truoc-$STAMP.txt"
docker image tag "$(docker inspect --format '{{.Image}}' thermal-node-exporter)" \
  "thermal-rollback/node-exporter:$STAMP"
docker image tag "$(docker inspect --format '{{.Image}}' thermal-prometheus)" \
  "thermal-rollback/prometheus:$STAMP"
docker image tag "$(docker inspect --format '{{.Image}}' thermal-grafana)" \
  "thermal-rollback/grafana:$STAMP"

docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:9090/-/ready
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  thermal-node-exporter thermal-prometheus thermal-grafana \
  | tee "$HOME/thermal-monitoring-images-sau-$STAMP.txt"
```

Nếu nghiệm thu thất bại và cần quay lại image vừa lưu, tạo override tạm thời
với đúng giá trị `STAMP` của lượt cập nhật rồi khởi động lại stack:

```bash
STAMP="<giá-trị-đã-ghi-lại-khi-pull-image>"
cat > /tmp/thermal-monitoring-rollback.yaml <<YAML
services:
  node-exporter:
    image: thermal-rollback/node-exporter:$STAMP
  prometheus:
    image: thermal-rollback/prometheus:$STAMP
  grafana:
    image: thermal-rollback/grafana:$STAMP
YAML

docker compose -f compose.yaml -f /tmp/thermal-monitoring-rollback.yaml up -d
docker compose -f compose.yaml -f /tmp/thermal-monitoring-rollback.yaml ps
curl -fsS http://127.0.0.1:9090/-/ready
```

Sau khi image mới đã hoạt động ổn định và không còn cần rollback, có thể xóa
các tag `thermal-rollback/*:$STAMP` trong một đợt bảo trì riêng.

## 16. Đặt lại dữ liệu lưu trữ

Các thao tác trong mục này xóa dữ liệu Docker volume của stack giám sát nhưng
không xóa repository `~/thermal-sensors`, cấu hình đã cài trong
`/opt/thermal-monitoring`, collector `systemd`, `mbpfan` hoặc tài nguyên
Kubernetes. Chạy sao lưu tại mục 14 trước nếu còn cần lịch sử cũ.

Tệp textfile `/var/lib/thermal-sensors/textfile/thermal_sensors.prom` chỉ chứa
mẫu cảm biến mới nhất, không phải lịch sử dashboard; không cần xóa tệp này
khi reset volume.

### 16.1. Xóa lịch sử Prometheus, giữ nguyên Grafana

Dùng lựa chọn này khi muốn xóa toàn bộ chuỗi thời gian nhiệt độ/RPM cũ nhưng
giữ tài khoản, datasource và trạng thái Grafana hiện có:

```bash
cd /opt/thermal-monitoring
docker compose stop prometheus
docker compose rm -f prometheus
docker volume rm thermal-monitoring-prometheus-data
docker compose up -d prometheus
curl -fsS http://127.0.0.1:9090/-/ready
```

Sau khi Prometheus thu thập lại, dashboard vẫn mở được nhưng chỉ có dữ liệu
mới kể từ thời điểm reset.

### 16.2. Khởi tạo lại Grafana, giữ lịch sử Prometheus

Dùng lựa chọn này khi muốn xóa cơ sở dữ liệu Grafana, ví dụ để áp dụng lại
mật khẩu admin từ secret. Datasource và dashboard provisioning sẽ được nạp
lại từ các tệp cấu hình; các thay đổi tạo thủ công trong giao diện Grafana sẽ
mất:

```bash
cd /opt/thermal-monitoring
docker compose stop grafana
docker compose rm -f grafana
docker volume rm thermal-monitoring-grafana-data
docker compose up -d grafana
curl -I http://100.120.64.5:3000/login
```

Sau bước này, đăng nhập bằng user `admin` và mật khẩu hiện có trong
`/opt/thermal-monitoring/secrets/grafana_admin_password.txt`.

### 16.3. Reset toàn bộ dữ liệu Prometheus và Grafana

Dùng lựa chọn này khi muốn bắt đầu dashboard mới hoàn toàn nhưng vẫn giữ
collector và cấu hình triển khai:

```bash
cd /opt/thermal-monitoring
docker compose down
docker volume rm \
  thermal-monitoring-prometheus-data \
  thermal-monitoring-grafana-data
docker compose up -d
curl -fsS http://127.0.0.1:9090/-/ready
curl -I http://100.120.64.5:3000/login
```

Chạy lại các bước nghiệm thu tại mục 10. Grafana sử dụng lại mật khẩu từ
secret; Prometheus bắt đầu lưu lịch sử mới từ lần scrape tiếp theo.

## 17. Gỡ bỏ

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

## 18. Xử lý sự cố nhanh

| Hiện tượng | Kiểm tra | Hành động trong phạm vi stack |
| --- | --- | --- |
| Dashboard không có dữ liệu | `thermal_collector_success`, `journalctl -u thermal-sensors.service` | Sửa lỗi đọc `sensors` hoặc quyền thư mục textfile, rồi chạy lại collector |
| Target Prometheus down | `docker compose logs node-exporter prometheus` | Xác nhận file `.prom` và network Compose |
| Grafana không truy cập được từ tailnet | `tailscale ip -4`, `ss -ltnp`, log Grafana | Xác nhận IP binding và kết nối Tailscale |
| `install: invalid user: '472'` khi tạo secret | Output `docker run --entrypoint id grafana/grafana:latest` | Không tạo user host `472`; tạo lại secret theo mục 8 trong thư mục mode `0700`, với file mode `0444` |
| Đã thay secret nhưng mật khẩu Grafana không đổi | Kiểm tra volume Grafana có được khởi tạo trước khi thay secret không | Secret chỉ đặt mật khẩu lần đầu; reset admin hoặc khởi tạo lại Grafana theo mục 16.2 |
| `thermal_mbpfan_config_valid` bằng `0` | Quyền đọc và nội dung `/etc/mbpfan.conf`, log collector | Khôi phục quyền đọc cho user dịch vụ hoặc sửa cấu hình tại quy trình quản trị `mbpfan`; không chạy collector bằng root |
| Nhiệt độ Apple SMC bất thường | `sensors` và file `.prom` | Xóa key khỏi allowlist, không xuất sensor thô chưa xác minh |
| RPM khác kỳ vọng `mbpfan` | sysfs, `/etc/mbpfan.conf`, log `mbpfan` | Điều tra dịch vụ quạt riêng, không chỉnh từ dashboard |
| Dữ liệu chiếm đĩa nhanh | Volume Docker và cờ retention | Xác nhận giới hạn `15d`/`2GB`, sao lưu trước khi can thiệp |

## 19. Tham chiếu chính thức

- [Cấu hình Grafana Docker bằng secret file](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/)
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)
- [Giới hạn UID/GID/mode của file secret trong Compose](https://docs.docker.com/reference/compose-file/services/#secrets)

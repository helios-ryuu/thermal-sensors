#!/usr/bin/env bash
set -euo pipefail

echo "======================================================"
echo "   🧹 DỌN DẸP CÁC TỆP VÀ SERVICE PHIÊN BẢN CŨ TRÊN HOST "
echo "======================================================"

# Kiểm tra quyền sudo nếu cần xóa tệp hệ thống
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

echo "[1/4] Dừng và vô hiệu hóa các systemd service/timer cũ..."
$SUDO systemctl disable --now thermal-sensors.timer thermal-sensors.service thermal-monitoring.service 2>/dev/null || true

echo "[2/4] Xóa các file systemd unit cũ..."
$SUDO rm -f /etc/systemd/system/thermal-sensors.service \
            /etc/systemd/system/thermal-sensors.timer \
            /etc/systemd/system/thermal-monitoring.service
$SUDO systemctl daemon-reload

echo "[3/4] Xóa thư mục cài đặt cũ /opt và /var/lib..."
$SUDO rm -rf /opt/thermal-monitoring /var/lib/thermal-sensors

echo "[4/4] Xóa Docker volume cũ nếu còn..."
docker volume rm thermal-monitoring-prometheus-data thermal-monitoring-grafana-data 2>/dev/null || true

echo ""
echo "======================================================"
echo "  [✓] Hoàn tất dọn dẹp hệ thống cũ!"
echo "  Bây giờ bạn có thể dùng './scripts/setup.sh' để chạy phiên bản 100% Docker Compose mới."
echo "======================================================"


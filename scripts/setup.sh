#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

echo "======================================================"
echo "      🚀 THIẾT LẬP HỆ THỐNG GIÁM SÁT NHIỆT ĐỘ        "
echo "======================================================"

# 1. Khởi tạo file .env nếu chưa tồn tại
if [ ! -f .env ]; then
    echo "[+] Đang tạo file .env từ .env.example..."
    sed "s|/home/user/.sens|${HOME}/.sens|g" .env.example > .env
fi

# Load các biến môi trường
set -a
# shellcheck disable=SC1091
source .env
set +a

# Mở rộng dấu ~ hoặc biến ${HOME} trong SENS_DATA_DIR nếu có
SENS_DATA_DIR="${SENS_DATA_DIR/#\~/$HOME}"
SENS_DATA_DIR="${SENS_DATA_DIR:-${HOME}/.sens}"

echo "[+] Thư mục lưu trữ dữ liệu: ${SENS_DATA_DIR}"

# 2. Tạo các thư mục lưu trữ tập trung
mkdir -p "${SENS_DATA_DIR}/textfile"
mkdir -p "${SENS_DATA_DIR}/prometheus"
mkdir -p "${SENS_DATA_DIR}/grafana"

# Cấp quyền ghi phù hợp cho các tiến trình trong container (Prometheus: 65534, Grafana: 472)
chmod 777 "${SENS_DATA_DIR}/prometheus" "${SENS_DATA_DIR}/grafana" "${SENS_DATA_DIR}/textfile" 2>/dev/null || true

# 3. Kiểm tra biến môi trường quan trọng
if [ -z "${GRAFANA_ADMIN_PASSWORD:-}" ] || [ "${GRAFANA_ADMIN_PASSWORD}" = "change_this_admin_password_123" ]; then
    echo "⚠️  CẢNH BÁO: GRAFANA_ADMIN_PASSWORD đang dùng giá trị mặc định hoặc trống trong file .env."
    echo "    Bạn nên đổi mật khẩu an toàn trong file .env."
fi

if [ -z "${TUNNEL_TOKEN:-}" ] || [ "${TUNNEL_TOKEN}" = "your_cloudflare_tunnel_token_here" ]; then
    echo ""
    echo "⚠️  LƯU Ý: TUNNEL_TOKEN chưa được cấu hình trong file .env."
    echo "    Container 'cloudflared_tunnel' sẽ yêu cầu TUNNEL_TOKEN hợp lệ để kết nối."
    echo ""
fi

# 4. Build và khởi động Docker Compose
echo "[+] Đang build collector và khởi động Docker Compose..."
docker compose build collector
docker compose up -d

echo ""
echo "======================================================"
echo "              ✅ TRIỂN KHAI THÀNH CÔNG!              "
echo "======================================================"
echo "Grafana Dashboard : http://${GRAFANA_BIND_IP:-100.120.64.5}:${GRAFANA_PORT:-3000}"
echo "User đăng nhập    : ${GRAFANA_ADMIN_USER:-admin}"
echo "Mật khẩu Admin    : [Được cấu hình trong file .env]"
echo "Prometheus Metric : http://${PROMETHEUS_BIND_IP:-127.0.0.1}:${PROMETHEUS_PORT:-9090}"
echo "Dữ liệu lưu tại   : ${SENS_DATA_DIR}"
echo "Cloudflare Tunnel : Đang chạy với container 'cloudflared_tunnel'"
echo "======================================================"

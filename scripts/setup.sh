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
mkdir -p "${SENS_DATA_DIR}/secrets"

# Cấp quyền ghi phù hợp cho các tiến trình trong container (Prometheus: 65534, Grafana: 472)
chmod 777 "${SENS_DATA_DIR}/prometheus" "${SENS_DATA_DIR}/grafana" "${SENS_DATA_DIR}/textfile" 2>/dev/null || true
chmod 755 "${SENS_DATA_DIR}/secrets" 2>/dev/null || true

# 3. Tạo mật khẩu Grafana Admin nếu chưa có
SECRET_FILE="${SENS_DATA_DIR}/secrets/grafana_admin_password.txt"
PASSWORD_CREATED=0

if [ ! -f "${SECRET_FILE}" ] || [ ! -s "${SECRET_FILE}" ]; then
    echo "[+] Đang sinh mật khẩu admin Grafana ngẫu nhiên..."
    if command -v openssl >/dev/null 2>&1; then
        GRAFANA_PASS="$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 20)"
    else
        GRAFANA_PASS="$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 20)"
    fi
    printf "%s" "${GRAFANA_PASS}" > "${SECRET_FILE}"
    chmod 644 "${SECRET_FILE}"
    PASSWORD_CREATED=1
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

if [ "${PASSWORD_CREATED}" -eq 1 ]; then
    echo "Mật khẩu Admin    : $(cat "${SECRET_FILE}")"
    echo "⚠️  HÃY LƯU MẬT KHẨU TRÊN VÀO TRÌNH QUẢN LÝ MẬT KHẨU CỦA BẠN!"
else
    echo "Mật khẩu Admin    : [Đã tồn tại trong ${SECRET_FILE}]"
fi

echo "Prometheus Metric : http://${PROMETHEUS_BIND_IP:-127.0.0.1}:${PROMETHEUS_PORT:-9090}"
echo "Dữ liệu lưu tại   : ${SENS_DATA_DIR}"
echo "======================================================"


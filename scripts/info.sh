#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

# 1. Load các biến môi trường từ .env nếu có
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Mở rộng dấu ~ hoặc biến ${HOME} trong SENS_DATA_DIR nếu có
SENS_DATA_DIR="${SENS_DATA_DIR/#\~/$HOME}"
if [ "${SENS_DATA_DIR}" = "/home/user/.sens" ] && [ ! -d "/home/user/.sens" ]; then
    SENS_DATA_DIR="${HOME}/.sens"
fi
SENS_DATA_DIR="${SENS_DATA_DIR:-${HOME}/.sens}"

# Tự động phát hiện IP Tailscale của máy
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
if [ -z "${TAILSCALE_IP}" ]; then
    TAILSCALE_IP="100.120.64.5"
fi

GRAFANA_IP="${GRAFANA_BIND_IP:-${TAILSCALE_IP}}"
GRAFANA_P="${GRAFANA_PORT:-3000}"
PROM_IP="${PROMETHEUS_BIND_IP:-127.0.0.1}"
PROM_P="${PROMETHEUS_PORT:-9090}"
ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"

echo ""
echo "======================================================"
echo "              ✅ TRIỂN KHAI THÀNH CÔNG!              "
echo "======================================================"
echo "Grafana Dashboard : http://${GRAFANA_IP}:${GRAFANA_P}"
echo "Truy cập qua mạng : Tailscale (${GRAFANA_IP}:${GRAFANA_P})"
echo "User đăng nhập    : ${ADMIN_USER}"
echo "Mật khẩu Admin    : [Được cấu hình trong file .env]"
echo "Prometheus Metric : http://${PROM_IP}:${PROM_P}"
echo "Dữ liệu lưu tại   : ${SENS_DATA_DIR}"
echo "======================================================"

get_dir_size() {
    local target="$1"
    if [ -d "$target" ]; then
        local size
        size=$( (du -sh "$target" 2>/dev/null || true) | awk '{print $1; exit}' )
        echo "${size:-N/A}"
    else
        echo "N/A"
    fi
}

# ======================================================
# METADATA DỮ LIỆU & DUNG LƯỢNG LƯU TRỮ
# ======================================================
echo ""
echo "📊 METADATA DỮ LIỆU & LƯU TRỮ:"
echo "------------------------------------------------------"
if [ -d "${SENS_DATA_DIR}" ]; then
    TOTAL_DISK=$(get_dir_size "${SENS_DATA_DIR}")
    PROM_DISK=$(get_dir_size "${SENS_DATA_DIR}/prometheus")
    GRAF_DISK=$(get_dir_size "${SENS_DATA_DIR}/grafana")
    TEXT_DISK=$(get_dir_size "${SENS_DATA_DIR}/textfile")

    echo "  • Tổng dung lượng lưu trữ : ${TOTAL_DISK}"
    echo "  • Prometheus TSDB (15d)   : ${PROM_DISK}"
    echo "  • Grafana Database        : ${GRAF_DISK}"
    echo "  • Textfile Metrics        : ${TEXT_DISK}"
else
    echo "  ⚠️ Thư mục dữ liệu chưa tồn tại: ${SENS_DATA_DIR}"
fi

PROM_FILE="${SENS_DATA_DIR}/textfile/thermal_sensors.prom"
if [ -f "${PROM_FILE}" ]; then
    LAST_MOD=$(stat -c '%y' "${PROM_FILE}" 2>/dev/null | cut -d'.' -f1 || stat -f "%Sm" "${PROM_FILE}" 2>/dev/null || echo "N/A")
    METRIC_COUNT=$(grep -cv '^#' "${PROM_FILE}" 2>/dev/null || echo "0")
    
    echo ""
    echo "🌡️ METADATA CẢM BIẾN (Lần ghi gần nhất: ${LAST_MOD}):"
    echo "------------------------------------------------------"
    echo "  • Tổng số mẫu metrics     : ${METRIC_COUNT}"
    
    AMBIENT=$(grep '^thermal_estimated_ambient_temperature_celsius' "${PROM_FILE}" 2>/dev/null | awk '{print $2}' || true)
    CPU_PKG=$(grep '^thermal_temperature_celsius{component="cpu",sensor="package"}' "${PROM_FILE}" 2>/dev/null | awk '{print $2}' || true)
    GPU_EDGE=$(grep '^thermal_temperature_celsius{component="gpu",sensor="edge"}' "${PROM_FILE}" 2>/dev/null | awk '{print $2}' || true)
    FAN_RPM=$(grep '^thermal_fan_speed_rpm' "${PROM_FILE}" 2>/dev/null | awk '{printf "%.0f\n", $2}' || true)
    
    [ -n "${AMBIENT}" ] && echo "  • Nhiệt độ phòng (~Ambient): ${AMBIENT} °C"
    [ -n "${CPU_PKG}" ] && echo "  • CPU Package              : ${CPU_PKG} °C"
    [ -n "${GPU_EDGE}" ] && echo "  • GPU Edge                 : ${GPU_EDGE} °C"
    [ -n "${FAN_RPM}" ] && echo "  • Tốc độ quạt (Main)       : ${FAN_RPM} RPM"
fi

echo ""
echo "🐳 TRẠNG THÁI CONTAINER:"
echo "------------------------------------------------------"
if command -v docker >/dev/null 2>&1; then
    docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || docker compose ps 2>/dev/null || echo "  (Chưa có container nào đang chạy)"
else
    echo "  (Docker chưa được cài đặt)"
fi
echo "======================================================"

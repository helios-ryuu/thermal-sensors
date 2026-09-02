#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

PURGE=0

for arg in "$@"; do
    case "${arg}" in
        -p|--purge)
            PURGE=1
            ;;
        -h|--help)
            echo "Cách dùng: $0 [tuỳ chọn]"
            echo "Tuỳ chọn:"
            echo "  (không truyền)   Dừng và gỡ bỏ các container Docker Compose"
            echo "  -p, --purge      Dừng container VÀ xóa toàn bộ dữ liệu trong thư mục SENS_DATA_DIR"
            echo "  -h, --help       Hiển thị trợ giúp này"
            exit 0
            ;;
        *)
            echo "Tùy chọn không hợp lệ: ${arg}"
            echo "Dùng '$0 --help' để xem hướng dẫn."
            exit 1
            ;;
    esac
done

# Load SENS_DATA_DIR từ .env nếu có
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

SENS_DATA_DIR="${SENS_DATA_DIR/#\~/$HOME}"
SENS_DATA_DIR="${SENS_DATA_DIR:-${HOME}/.sens}"

echo "[+] Đang dừng các container Docker Compose..."
docker compose down

if [ "${PURGE}" -eq 1 ]; then
    echo ""
    echo "⚠️  CẢNH BÁO: Bạn sắp xóa toàn bộ dữ liệu giám sát và mật khẩu tại:"
    echo "    ${SENS_DATA_DIR}"
    read -r -p "Bạn có chắc chắn muốn xóa không? [y/N]: " CONFIRM
    if [[ "${CONFIRM}" =~ ^[yY]([eE][sS])?$ ]]; then
        echo "[+] Đang xóa thư mục dữ liệu: ${SENS_DATA_DIR}..."
        rm -rf "${SENS_DATA_DIR}"
        echo "[✓] Đã dọn dẹp sạch sẽ toàn bộ dữ liệu."
    else
        echo "[-] Đã hủy thao tác xóa dữ liệu."
    fi
else
    echo "[✓] Đã dừng stack thành công. (Dữ liệu vẫn được giữ tại ${SENS_DATA_DIR})"
    echo "    Để xóa sạch toàn bộ dữ liệu, chạy: $0 --purge"
fi


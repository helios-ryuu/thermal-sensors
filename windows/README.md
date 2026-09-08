# Hướng dẫn Vận hành Hệ thống Giám sát & Hộp đen Chẩn đoán Sập nguồn (Windows)

Hệ thống được thiết kế theo kiến trúc **Native Portable Stack** dành cho máy tính để bàn (Desktop PC) chạy Windows 10/11:
- **Zero-Reboot**: Tuyệt đối không yêu cầu khởi động lại máy, bảo toàn 100% kết nối Remote Desktop/SSH.
- **Tần số quét cao 5s (Blackbox Flight Recorder)**: Bắt trọn khoảnh khắc sụt áp đường 12V của PSU, vọt công suất đỉnh GPU Watts và GPU Hotspot chạm ngưỡng ngắt bảo vệ trước thời điểm máy sập.
- **Chẩn đoán Event Log (Post-Mortem Engine)**: Bóc tách mã lỗi `BugcheckCode` từ Kernel-Power Event ID 41, mã dừng BSOD ngầm và lỗi phần cứng WHEA.

---

## 1. Các bước triển khai nhanh qua SSH / PowerShell

Mở terminal PowerShell (Run as Administrator) trên máy Windows và thực hiện các bước sau:

### Bước 1: Cài đặt thư viện Python
```powershell
cd thermal-sensors\windows
python -m pip install -r requirements.txt
```

### Bước 2: Tải và giải nén các gói Portable (1-Click Setup)
Script sẽ tự động tải bản portable của **Prometheus**, **Grafana** và **NSSM** về thư mục cục bộ `windows\bin\` (không cài vào Program Files, không sửa Registry):
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

### Bước 3: Đăng ký Windows Services chạy ngầm
Sử dụng NSSM để biến 3 tiến trình thành dịch vụ hệ thống tự khởi động cùng Windows (kể cả khi chưa đăng nhập màn hình GUI):
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-services.ps1
```

### Bước 4: Khởi động và kiểm tra
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1
```

---

## 2. Truy cập & Sử dụng các Dashboards trên Grafana

Mở trình duyệt truy cập: **`http://<IP-may-Windows>:3000`**
*(Tài khoản mặc định: `admin` / `admin`)*

Hệ thống đã tự động nạp sẵn 4 Dashboard chuyên biệt:

### 1. `Windows - Blackbox Crash Diagnostics & Flight Recorder` (Ưu tiên số 1)
- **Status Cards**: Hiển thị rõ máy vừa boot sạch (`CLEAN BOOT`) hay bị sập nguồn (`SẬP NGUỒN ĐỘT NGỘT - Event ID 41`).
- **Phân loại BugCheck**:
  - `0x00000000`: Cắt nguồn tức thì do PSU quá tải / sụt áp đường 12V hoặc ngắt nhiệt Tjunction.
  - `0x00000116` (`VIDEO_TDR_FAILURE`): Driver GPU (nvlddmkm.sys) bị crash do quá tải hoặc quá nhiệt VRAM.
  - `0x00000124` (`WHEA_UNCORRECTABLE_ERROR`): Lỗi phần cứng vật lý bus PCIe hoặc CPU.
- **Đồ thị đường 12V**: Đánh giá nguồn PSU (Xanh: 11.7V–12.3V; Vàng: 11.4V–11.7V; Đỏ: < 11.4V).
- **Đồ thị Hotspot Delta**: So sánh `GPU Hotspot - GPU Core`. Cảnh báo đỏ nếu chênh lệch > 25°C (khô keo tản nhiệt GPU).
- **Đồ thị Công suất đỉnh (Watts)**: Theo dõi cú vọt công suất của GPU + CPU ngay trước giây phút sập nguồn.

### 2. `Windows - Hardware Thermals & Fan Speeds`
- Theo dõi toàn diện nhiệt độ Core, Hotspot, VRAM của card đồ họa NVIDIA.
- Tốc độ quạt tản nhiệt GPU (%) và nhiệt độ CPU Package, ổ cứng NVMe.

### 3. `Windows - System Health & Resources`
- Giám sát CPU % tổng thể và từng nhân, RAM sử dụng.
- **Pagefile / Commit Limit**: Phát hiện sớm hiện tượng rò rỉ bộ nhớ (Memory Leak) gây sập hệ điều hành.
- Dung lượng và tốc độ đọc/ghi các phân vùng ổ đĩa `C:\`, `D:\`.

### 4. `Windows - Network & Security`
- **Latency Dials đa điểm**: Đo độ trễ tới Gateway, Local DNS, Viettel, Cloudflare, Google, YouTube, Facebook, Discord, AWS Asia, GitHub (với ngưỡng màu tối ưu < 80ms hiển thị xanh, chống mất dial khi mạng jitter).
- **Open Listening Ports**: Bảng danh sách cổng mạng kèm chính xác tên tiến trình Windows (`chrome.exe`, `discord.exe`, `steam.exe`...).

---

## 3. Lệnh Quản trị & Vận hành Thường nhật qua SSH

| Tác vụ | Lệnh thực thi |
| :--- | :--- |
| **Kiểm tra trạng thái & Log** | `powershell -ExecutionPolicy Bypass -File .\scripts\status.ps1` |
| **Khởi động toàn bộ Services** | `powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1` |
| **Dừng toàn bộ Services** | `powershell -ExecutionPolicy Bypass -File .\scripts\stop-services.ps1` |
| **Xem log trực tiếp của Agent** | `Get-Content .\logs\agent.log -Wait -Tail 30` |
| **Gỡ bỏ sạch sẽ Services** | `powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-services.ps1` |


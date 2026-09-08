#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Unified Monitoring Agent & Blackbox Crash Flight Recorder
Thu thập toàn bộ dữ liệu phần cứng, tài nguyên, mạng và nhật ký sập nguồn (Kernel-Power Event ID 41).
Xuất dữ liệu chuẩn Prometheus tại endpoint: http://127.0.0.1:9100/metrics
"""

import os
import sys
import time
import re
import json
import socket
import logging
import threading
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Force UTF-8 on Windows console / NSSM service streams to prevent charmap/cp1252 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Import thư viện bổ trợ nếu có
try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Cấu hình chu kỳ
METRICS_PORT = int(os.environ.get("AGENT_PORT", "9100"))
BIND_IP = os.environ.get("AGENT_BIND_IP", "0.0.0.0")
FAST_INTERVAL = float(os.environ.get("AGENT_FAST_INTERVAL", "5"))   # 5s: Thermals, Watts, Voltages, CPU, RAM
SLOW_INTERVAL = float(os.environ.get("AGENT_SLOW_INTERVAL", "60"))  # 60s: EventLog, WAN/ISP, Ping Dials, Traceroute

# Danh sách đích ping độ trễ đa điểm (Kế thừa từ bộ lọc ổn định)
LATENCY_TARGETS = [
    ("vn_viettel", "Viettel DNS", "203.113.131.1", "domestic_vn"),
    ("vn_vnpt", "VNPT DNS", ["203.162.0.181", "203.162.4.190"], "domestic_vn"),
    ("cloudflare", "Cloudflare (1.1.1.1)", "1.1.1.1", "global_dns"),
    ("google", "Google (8.8.8.8)", "8.8.8.8", "global_dns"),
    ("youtube", "YouTube", "youtube.com", "media_service"),
    ("facebook", "Facebook", ["facebook.com", "m.facebook.com"], "media_service"),
    ("discord", "Discord", "discord.com", "chat_service"),
    ("aws_asia", "AWS Asia (Singapore)", "s3.ap-southeast-1.amazonaws.com", "cloud_service"),
    ("github", "GitHub", "github.com", "developer_service"),
]

# Bảng dịch mã Bugcheck Code của Windows Kernel-Power
KNOWN_BUGCHECKS = {
    0x00000000: ("HARD_POWER_OFF_OR_THERMAL_TRIP", "Tắt phụt nguồn đột ngột do PSU sụt áp / quá tải OCP hoặc chạm ngưỡng ngắt nhiệt Tjunction"),
    0x00000116: ("VIDEO_TDR_FAILURE", "GPU Driver (nvlddmkm.sys) bị treo hoặc crash do quá nhiệt VRAM/xung nhịp không ổn định"),
    0x00000124: ("WHEA_UNCORRECTABLE_ERROR", "Lỗi phần cứng vật lý bo mạch chủ, bus PCIe hoặc CPU Machine Check Exception"),
    0x00000050: ("PAGE_FAULT_IN_NONPAGED_AREA", "Lỗi truy cập bộ nhớ RAM hoặc xung đột driver hệ thống"),
    0x0000003B: ("SYSTEM_SERVICE_EXCEPTION", "Lỗi biệt lệ kernel khi thực thi mã đồ họa hoặc driver"),
    0x0000000A: ("IRQL_NOT_LESS_OR_EQUAL", "Driver truy cập địa chỉ nhớ không hợp lệ tại mức IRQL cao"),
    0x0000001E: ("KMODE_EXCEPTION_NOT_HANDLED", "Biệt lệ kernel chưa được xử lý"),
    0x0000007E: ("SYSTEM_THREAD_EXCEPTION", "Tiến trình hệ thống bị gián đoạn do lỗi driver"),
    0x000000D1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "Driver mạng hoặc GPU gây crash phân vùng nhớ"),
    0x0000009F: ("DRIVER_POWER_STATE_FAILURE", "Lỗi chuyển đổi trạng thái nguồn/Sleep/Hibernate của driver"),
}

# Cache toàn cục chia sẻ giữa thread thu thập và HTTP Server
state_lock = threading.Lock()
metrics_output_text = ""
slow_cache = {
    "last_run": 0,
    "crashes": [],
    "last_crash_info": {},
    "whea_count": 0,
    "last_reboot_unexpected": 0,
    "public_ipv4": "None",
    "public_ipv6": "None",
    "isp": "unknown",
    "double_nat": 0,
    "cgnat": 0,
    "latencies": {},
    "latency_fail_counts": {},
}


# ==============================================================================
# 1. HỘP ĐEN KHÁM NGHIỆM EVENT LOG (POST-MORTEM CRASH ENGINE)
# ==============================================================================

def parse_bugcheck_code(code_val):
    """Chuyển đổi số nguyên BugcheckCode sang mã Hex và tên định danh."""
    try:
        if isinstance(code_val, str):
            code_int = int(code_val, 0)
        else:
            code_int = int(code_val)
    except Exception:
        code_int = 0

    hex_str = f"0x{code_int:08X}"
    name, desc = KNOWN_BUGCHECKS.get(
        code_int,
        (f"BUGCHECK_{hex_str}", f"Lỗi BSOD mã {hex_str}")
    )
    return code_int, hex_str, name, desc


def query_windows_crash_events():
    """
    Truy vấn Windows System Event Log để lấy lịch sử Kernel-Power Event 41
    và các sự kiện sập nguồn bất thường (ID 6008, 1001, WHEA).
    """
    crashes = []
    last_crash = {}
    is_unexpected_boot = 0
    whea_total = 0

    # Lệnh PowerShell nhẹ để lấy tối đa 10 sự kiện Kernel-Power Event ID 41
    ps_cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        """
        try {
            $events = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41} -MaxEvents 10 -ErrorAction SilentlyContinue
            if ($events) {
                $events | ForEach-Object {
                    $xml = [xml]$_.ToXml()
                    $eventData = $xml.Event.EventData.Data
                    $bCode = 0
                    $bParam1 = '0x0'
                    if ($eventData) {
                        foreach ($d in $eventData) {
                            if ($d.Name -eq 'BugcheckCode') { $bCode = $d.'#text' }
                            if ($d.Name -eq 'BugcheckParameter1') { $bParam1 = $d.'#text' }
                        }
                    }
                    [PSCustomObject]@{
                        TimeCreated = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
                        Epoch = [int64]($_.TimeCreated.ToUniversalTime() - (Get-Date '1970-01-01')).TotalSeconds
                        BugcheckCode = $bCode
                        BugcheckParam1 = $bParam1
                    }
                } | ConvertTo-Json -Compress
            } else { '[]' }
        } catch { '[]' }
        """
    ]

    try:
        out = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=8)
        if out.returncode == 0 and out.stdout.strip():
            raw_text = out.stdout.strip()
            # Xử lý khi PowerShell trả về 1 object đơn lẻ thay vì mảng JSON
            if raw_text.startswith("{") and raw_text.endswith("}"):
                items = [json.loads(raw_text)]
            elif raw_text.startswith("[") and raw_text.endswith("]"):
                items = json.loads(raw_text)
            else:
                items = []

            for it in items:
                b_int, b_hex, b_name, b_desc = parse_bugcheck_code(it.get("BugcheckCode", 0))
                crash_entry = {
                    "time": it.get("TimeCreated", "unknown"),
                    "epoch": it.get("Epoch", 0),
                    "bugcheck_code": b_int,
                    "bugcheck_hex": b_hex,
                    "bugcheck_name": b_name,
                    "bugcheck_desc": b_desc,
                    "bugcheck_param1": it.get("BugcheckParam1", "0x0"),
                }
                crashes.append(crash_entry)

            if crashes:
                last_crash = crashes[0]
                # Nếu lần crash gần nhất xảy ra trong vòng 15 phút sau khi máy boot -> đánh dấu unexpected
                boot_time = psutil.boot_time() if psutil else 0
                if abs(last_crash["epoch"] - boot_time) < 1800:
                    is_unexpected_boot = 1
    except Exception as e:
        logging.warning(f"Error reading Event ID 41 from Event Log: {e}")

    # Đếm số lỗi phần cứng WHEA trong 24h
    whea_cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        """
        try {
            $d = (Get-Date).AddDays(-1)
            $c = (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$d} -ErrorAction SilentlyContinue).Count
            if ($c) { $c } else { 0 }
        } catch { 0 }
        """
    ]
    try:
        w_out = subprocess.run(whea_cmd, capture_output=True, text=True, timeout=5)
        if w_out.returncode == 0 and w_out.stdout.strip().isdigit():
            whea_total = int(w_out.stdout.strip())
    except Exception:
        pass

    return crashes, last_crash, is_unexpected_boot, whea_total


# ==============================================================================
# 2. GIÁM SÁT CARD ĐỒ HỌA NVIDIA (CORE, HOTSPOT, VRAM, WATTS, THROTTLE)
# ==============================================================================

_nvml_initialized = False

def init_nvml():
    global _nvml_initialized
    if _nvml_initialized:
        return True
    if pynvml:
        try:
            pynvml.nvmlInit()
            _nvml_initialized = True
            return True
        except Exception as e:
            logging.debug(f"Could not initialize pynvml: {e}")
    return False


def collect_nvidia_gpu_metrics():
    """
    Thu thập chỉ số GPU NVIDIA với trọng tâm:
    - GPU Core Temp, Hotspot Temp, VRAM/Memory Temp
    - Delta Hotspot (Hotspot - Core)
    - Power Draw (Watts) so với Power Limit
    - Fan Speed RPM & Percent
    - Throttle Reasons (Bảo vệ nhiệt / sụt nguồn)
    """
    gpus = []

    # Cách 1: Sử dụng pynvml (nhanh, chuẩn xác, trực tiếp từ nvml.dll)
    if init_nvml():
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            for idx in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")

                # Nhiệt độ Core
                try:
                    temp_core = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception:
                    temp_core = None

                # Nhiệt độ Hotspot (nếu driver/GPU hỗ trợ)
                temp_hotspot = None
                try:
                    # Sensor 1 thường là GPU Hotspot trên RTX 30/40
                    temp_hotspot = pynvml.nvmlDeviceGetTemperature(handle, 1)
                except Exception:
                    pass

                # Nhiệt độ VRAM / Memory
                temp_mem = None
                try:
                    # Sensor 2 thường là VRAM / Memory
                    temp_mem = pynvml.nvmlDeviceGetTemperature(handle, 2)
                except Exception:
                    pass

                # Công suất (Watts)
                power_w = None
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
                    power_w = round(power_mw / 1000.0, 2)
                except Exception:
                    pass

                power_limit_w = None
                try:
                    power_limit_mw = pynvml.nvmlDeviceGetPowerManagementLimit(handle)
                    power_limit_w = round(power_limit_mw / 1000.0, 2)
                except Exception:
                    pass

                # Quạt
                fan_pct = None
                try:
                    fan_pct = pynvml.nvmlDeviceGetFanSpeed(handle)
                except Exception:
                    pass

                # Tải & VRAM
                util_gpu = None
                try:
                    rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    util_gpu = rates.gpu
                except Exception:
                    pass

                mem_used = None
                mem_total = None
                try:
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    mem_used = mem_info.used
                    mem_total = mem_info.total
                except Exception:
                    pass

                # Xung nhịp
                clock_core = None
                clock_mem = None
                try:
                    clock_core = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                    clock_mem = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                except Exception:
                    pass

                gpus.append({
                    "index": str(idx),
                    "name": name,
                    "temp_core": temp_core,
                    "temp_hotspot": temp_hotspot,
                    "temp_mem": temp_mem,
                    "power_w": power_w,
                    "power_limit_w": power_limit_w,
                    "fan_pct": fan_pct,
                    "util_gpu": util_gpu,
                    "mem_used": mem_used,
                    "mem_total": mem_total,
                    "clock_core": clock_core,
                    "clock_mem": clock_mem,
                })
        except Exception as e:
            logging.debug(f"Error reading NVML: {e}")

    # Cách 2: Dự phòng qua nvidia-smi.exe nếu pynvml chưa có hoặc thiếu Hotspot
    if not gpus or any(g["temp_hotspot"] is None for g in gpus):
        smi_data = query_nvidia_smi()
        if smi_data:
            if not gpus:
                gpus = smi_data
            else:
                # Bổ sung Hotspot từ nvidia-smi nếu pynvml không lấy được
                for idx, g in enumerate(gpus):
                    if idx < len(smi_data):
                        if g["temp_hotspot"] is None and smi_data[idx].get("temp_hotspot") is not None:
                            g["temp_hotspot"] = smi_data[idx]["temp_hotspot"]
                        if g["temp_mem"] is None and smi_data[idx].get("temp_mem") is not None:
                            g["temp_mem"] = smi_data[idx]["temp_mem"]

    # Tính toán Hotspot Delta (Hotspot - Core) cho từng GPU
    for g in gpus:
        if g.get("temp_core") is not None and g.get("temp_hotspot") is not None:
            g["hotspot_delta"] = round(g["temp_hotspot"] - g["temp_core"], 2)
        else:
            g["hotspot_delta"] = 0.0

    return gpus


def query_nvidia_smi():
    """Fallback truy vấn nvidia-smi CLI trích xuất Core, Hotspot và Power."""
    results = []
    # nvidia-smi query
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,temperature.gpu,temperature.memory,power.draw,power.limit,utilization.gpu,memory.used,memory.total,clocks.current.graphics,clocks.current.memory,fan.speed",
        "--format=csv,noheader,nounits"
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
        if out.returncode == 0 and out.stdout.strip():
            for line in out.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 11:
                    def safe_float(v):
                        try:
                            return float(v)
                        except Exception:
                            return None

                    def safe_int(v):
                        try:
                            return int(float(v))
                        except Exception:
                            return None

                    results.append({
                        "index": parts[0],
                        "name": parts[1],
                        "temp_core": safe_float(parts[2]),
                        "temp_hotspot": None,
                        "temp_mem": safe_float(parts[3]),
                        "power_w": safe_float(parts[4]),
                        "power_limit_w": safe_float(parts[5]),
                        "util_gpu": safe_float(parts[6]),
                        "mem_used": safe_int(parts[7]) * 1024 * 1024 if safe_int(parts[7]) else None,
                        "mem_total": safe_int(parts[8]) * 1024 * 1024 if safe_int(parts[8]) else None,
                        "clock_core": safe_int(parts[9]),
                        "clock_mem": safe_int(parts[10]),
                        "fan_pct": safe_float(parts[11]) if len(parts) > 11 else None,
                    })
    except Exception:
        pass
    return results


# ==============================================================================
# 3. GIÁM SÁT NGUỒN (ĐƯỜNG 12V/5V/3.3V), CPU & Ổ CỨNG NVMe
# ==============================================================================

def collect_motherboard_and_cpu_power():
    """
    Truy vấn cảm biến điện áp PSU bo mạch chủ (đường 12V, 5V, 3.3V)
    và công suất tiêu thụ của CPU (Watts) qua WMI / LibreHardwareMonitor.
    """
    data = {
        "v12": None,
        "v5": None,
        "v33": None,
        "cpu_power_w": None,
        "cpu_temp_package": None,
        "nvme_temps": {},
    }

    # Thử đọc qua LibreHardwareMonitor WMI namespace nếu LHM đang chạy nền
    ps_cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        """
        try {
            $sensors = Get-CimInstance -Namespace 'root\\LibreHardwareMonitor' -ClassName Sensor -ErrorAction Stop
            $sensors | Select-Object Name, SensorType, Value | ConvertTo-Json -Compress
        } catch {
            try {
                $sensors = Get-CimInstance -Namespace 'root\\OpenHardwareMonitor' -ClassName Sensor -ErrorAction Stop
                $sensors | Select-Object Name, SensorType, Value | ConvertTo-Json -Compress
            } catch { '[]' }
        }
        """
    ]

    try:
        out = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            raw = out.stdout.strip()
            items = []
            if raw.startswith("{") and raw.endswith("}"):
                items = [json.loads(raw)]
            elif raw.startswith("[") and raw.endswith("]"):
                items = json.loads(raw)

            for s in items:
                name = str(s.get("Name", "")).lower()
                stype = str(s.get("SensorType", "")).lower()
                val = s.get("Value")
                if val is None:
                    continue
                try:
                    fval = float(val)
                except Exception:
                    continue

                # Điện áp
                if stype == "voltage":
                    if "+12v" in name or "12v" in name or "vin" in name:
                        if 10.0 <= fval <= 14.0:
                            data["v12"] = round(fval, 3)
                    elif "+5v" in name or "5v" in name:
                        if 4.0 <= fval <= 6.0:
                            data["v5"] = round(fval, 3)
                    elif "+3.3v" in name or "3.3v" in name or "3v" in name:
                        if 2.8 <= fval <= 3.8:
                            data["v33"] = round(fval, 3)

                # Công suất CPU (Watts)
                elif stype == "power":
                    if "cpu package" in name or "package" in name or "cpu total" in name:
                        data["cpu_power_w"] = round(fval, 2)

                # Nhiệt độ CPU
                elif stype == "temperature":
                    if "cpu package" in name or "package" in name or "core max" in name:
                        data["cpu_temp_package"] = round(fval, 1)
                    elif "nvme" in name or "ssd" in name or "drive" in name:
                        data["nvme_temps"][s.get("Name", "NVMe")] = round(fval, 1)

    except Exception:
        pass

    # Nếu WMI của LHM không có, thử fallback đọc nhiệt độ ACPI ThermalZone chuẩn của Windows
    if data["cpu_temp_package"] is None:
        try:
            acpi_cmd = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "(Get-CimInstance -Namespace 'root\\wmi' -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue).CurrentTemperature"
            ]
            acpi_out = subprocess.run(acpi_cmd, capture_output=True, text=True, timeout=4)
            if acpi_out.returncode == 0 and acpi_out.stdout.strip().isdigit():
                # MSAcpi trả về phần mười độ Kelvin (tenths of Kelvin)
                kelvin_tenths = float(acpi_out.stdout.strip())
                celsius = round((kelvin_tenths / 10.0) - 273.15, 1)
                if 0 < celsius < 125:
                    data["cpu_temp_package"] = celsius
        except Exception:
            pass

    return data


# ==============================================================================
# 4. GIÁM SÁT TÀI NGUYÊN HỆ THỐNG (PSUTIL / WINDOWS API)
# ==============================================================================

def collect_system_resources():
    """Thu thập CPU%, RAM, Pagefile (Committed), Phân vùng ổ C:, D: và IOPS."""
    data = {
        "cpu_percent_total": 0.0,
        "cpu_percent_cores": [],
        "ram_total": 0,
        "ram_used": 0,
        "ram_pct": 0.0,
        "pagefile_total": 0,
        "pagefile_used": 0,
        "pagefile_pct": 0.0,
        "disks": [],
        "disk_read_bytes_sec": 0,
        "disk_write_bytes_sec": 0,
        "uptime_sec": 0,
    }

    if psutil:
        try:
            data["cpu_percent_total"] = psutil.cpu_percent(interval=None)
            data["cpu_percent_cores"] = psutil.cpu_percent(percpu=True, interval=None)

            vm = psutil.virtual_memory()
            data["ram_total"] = vm.total
            data["ram_used"] = vm.used
            data["ram_pct"] = vm.percent

            sm = psutil.swap_memory()
            data["pagefile_total"] = sm.total
            data["pagefile_used"] = sm.used
            data["pagefile_pct"] = sm.percent

            data["uptime_sec"] = int(time.time() - psutil.boot_time())

            # Ổ đĩa C:, D:...
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or part.fstype == "":
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    data["disks"].append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                    })
                except Exception:
                    pass

            # Disk IO
            dio = psutil.disk_io_counters()
            if dio:
                data["disk_read_bytes"] = dio.read_bytes
                data["disk_write_bytes"] = dio.write_bytes
        except Exception as e:
            logging.debug(f"psutil error: {e}")

    return data


# ==============================================================================
# 5. GIÁM SÁT MẠNG, PORT & ĐỘ TRỄ LATENCY DIALS
# ==============================================================================

def ping_target_windows(target):
    """
    Đo ping trên Windows: Gửi 2 gói tin (-n 2), timeout 2000ms (-w 2000), ép IPv4 (-4).
    Lấy giá trị RTT nhỏ nhất trong các gói thành công để tránh jitter.
    """
    if not target:
        return None
    hosts = [target] if isinstance(target, str) else list(target)
    for h in hosts:
        try:
            out = subprocess.run(
                ["ping", "-4", "-n", "2", "-w", "2000", str(h)],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if out.returncode == 0 and out.stdout:
                # Regex bắt chuỗi time=XXms hoặc Minimum = XXms
                matches = re.findall(r"time[<=]([0-9.]+)\s*ms", out.stdout, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"Minimum\s*=\s*([0-9.]+)\s*ms", out.stdout, re.IGNORECASE)
                if matches:
                    return round(min(float(m) for m in matches), 3)
        except Exception:
            pass
    return None


def get_default_gateway_and_dns_windows():
    """Lấy IP Gateway và DNS từ Windows qua ipconfig hoặc Get-NetRoute."""
    gw = None
    dns = []
    try:
        out = subprocess.run(["ipconfig", "/all"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            lines = out.stdout.splitlines()
            for line in lines:
                if "Default Gateway" in line or "Cổng mặc định" in line:
                    m = re.search(r":\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line)
                    if m and not gw:
                        gw = m.group(1)
                elif "DNS Servers" in line or "Máy chủ DNS" in line:
                    m = re.search(r":\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line)
                    if m:
                        dns.append(m.group(1))
    except Exception:
        pass
    return gw, dns


def collect_listening_ports_windows():
    """
    Trích xuất danh sách cổng đang lắng nghe và map với tên tiến trình Windows
    (Ví dụ: 3000 -> grafana.exe, 9090 -> prometheus.exe, 22 -> sshd.exe, discord.exe...).
    """
    ports = []
    if not psutil:
        return ports

    try:
        connections = psutil.net_connections(kind="inet")
        seen = set()
        for c in connections:
            if c.status == psutil.CONN_LISTEN:
                ip, port = c.laddr.ip, c.laddr.port
                key = (c.type, port)
                if key in seen:
                    continue
                seen.add(key)

                proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
                proc_name = "-"
                if c.pid:
                    try:
                        p = psutil.Process(c.pid)
                        proc_name = p.name()
                    except Exception:
                        proc_name = f"PID:{c.pid}"

                exposure = "Public / LAN"
                if ip in ["127.0.0.1", "::1"] or ip.startswith("127."):
                    exposure = "Localhost Only"
                elif ip.startswith("100."):
                    exposure = "Tailscale Only"

                ports.append({
                    "port": str(port),
                    "proto": proto,
                    "process": proc_name,
                    "exposure": exposure,
                })
    except Exception:
        pass
    return ports


def get_public_ip_and_isp():
    """Lấy IP Public và ISP thông qua API ngoài."""
    pub_ip = "None"
    isp = "unknown"
    endpoints = [
        "https://ifconfig.co/json",
        "http://ip-api.com/json",
        "https://ipinfo.io/json",
    ]
    for url in endpoints:
        try:
            # Dùng curl native trên Windows
            out = subprocess.run(["curl", "-s", "--max-time", "3", url], capture_output=True, text=True, timeout=4)
            if out.returncode == 0 and out.stdout.strip().startswith("{"):
                d = json.loads(out.stdout.strip())
                pub_ip = d.get("ip") or d.get("query") or pub_ip
                cand_isp = d.get("isp") or d.get("asn_org") or d.get("org") or ""
                if cand_isp and cand_isp.lower() != "unknown":
                    isp = cand_isp.strip()
                    break
        except Exception:
            pass
    return pub_ip, isp


# ==============================================================================
# 6. ĐÓNG GÓI CHUẨN PROMETHEUS TEXT EXPOSITION
# ==============================================================================

def escape_label_value(val):
    if val is None:
        return ""
    # In Prometheus exposition format, \, ", and \n must be escaped
    return str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def add_sample(lines, name, val, labels=None):
    if val is None:
        return
    if labels:
        label_str = ",".join(f'{k}="{escape_label_value(v)}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{label_str}}} {val}")
    else:
        lines.append(f"{name} {val}")


def generate_prometheus_metrics():
    """Tổng hợp toàn bộ chỉ số thành nội dung text chuẩn Prometheus."""
    lines = []
    lines.append("# HELP windows_agent_build_info Thong tin phien ban Windows Unified Agent")
    lines.append("# TYPE windows_agent_build_info gauge")
    lines.append('windows_agent_build_info{os="windows",arch="amd64",version="1.0.0"} 1')

    # 1. Hộp đen Crash Diagnostics (Kernel-Power Event ID 41)
    lines.append("# HELP windows_last_reboot_unexpected Trang thai khoi dong bat thuong (1: Crash/Power loss, 0: Clean boot)")
    lines.append("# TYPE windows_last_reboot_unexpected gauge")
    add_sample(lines, "windows_last_reboot_unexpected", slow_cache.get("last_reboot_unexpected", 0))

    lines.append("# HELP windows_whea_errors_total Tong so loi phan cung WHEA ghi nhan trong 24h")
    lines.append("# TYPE windows_whea_errors_total gauge")
    add_sample(lines, "windows_whea_errors_total", slow_cache.get("whea_count", 0))

    last_crash = slow_cache.get("last_crash_info", {})
    if last_crash:
        lines.append("# HELP windows_last_crash_info Details about the most recent unexpected shutdown event")
        lines.append("# TYPE windows_last_crash_info gauge")
        add_sample(
            lines,
            "windows_last_crash_info",
            1,
            {
                "bugcheck_hex": last_crash.get("bugcheck_hex", "0x00000000"),
                "bugcheck_name": last_crash.get("bugcheck_name", "UNKNOWN"),
                "bugcheck_desc": last_crash.get("bugcheck_desc", ""),
                "time": last_crash.get("time", ""),
            }
        )

    for c in slow_cache.get("crashes", []):
        add_sample(
            lines,
            "windows_crash_events_total",
            1,
            {
                "time": c["time"],
                "bugcheck_hex": c["bugcheck_hex"],
                "bugcheck_name": c["bugcheck_name"],
            }
        )

    # 2. NVIDIA GPU Metrics (Nhiệt độ, Hotspot, Watts, Fan)
    gpus = collect_nvidia_gpu_metrics()
    for g in gpus:
        idx = g["index"]
        lbl = {"gpu_index": idx, "gpu_name": g["name"]}

        add_sample(lines, "gpu_temperature_celsius", g.get("temp_core"), lbl)
        add_sample(lines, "gpu_hotspot_temperature_celsius", g.get("temp_hotspot"), lbl)
        add_sample(lines, "gpu_memory_temperature_celsius", g.get("temp_mem"), lbl)
        add_sample(lines, "gpu_hotspot_delta_celsius", g.get("hotspot_delta"), lbl)
        add_sample(lines, "gpu_power_draw_watts", g.get("power_w"), lbl)
        add_sample(lines, "gpu_power_limit_watts", g.get("power_limit_w"), lbl)
        add_sample(lines, "gpu_fan_speed_percent", g.get("fan_pct"), lbl)
        add_sample(lines, "gpu_utilization_percent", g.get("util_gpu"), lbl)
        add_sample(lines, "gpu_memory_used_bytes", g.get("mem_used"), lbl)
        add_sample(lines, "gpu_memory_total_bytes", g.get("mem_total"), lbl)
        add_sample(lines, "gpu_clock_graphics_mhz", g.get("clock_core"), lbl)
        add_sample(lines, "gpu_clock_memory_mhz", g.get("clock_mem"), lbl)

    # 3. Bo mạch chủ, Đường điện áp 12V/5V/3.3V và CPU Package Power
    hw = collect_motherboard_and_cpu_power()
    add_sample(lines, "motherboard_voltage_volts", hw.get("v12"), {"rail": "12v"})
    add_sample(lines, "motherboard_voltage_volts", hw.get("v5"), {"rail": "5v"})
    add_sample(lines, "motherboard_voltage_volts", hw.get("v33"), {"rail": "3.3v"})
    add_sample(lines, "cpu_package_power_watts", hw.get("cpu_power_w"))
    add_sample(lines, "cpu_package_temperature_celsius", hw.get("cpu_temp_package"))
    for nv_name, nv_temp in hw.get("nvme_temps", {}).items():
        add_sample(lines, "nvme_temperature_celsius", nv_temp, {"disk": nv_name})

    # 4. Tài nguyên hệ thống (psutil)
    sys_res = collect_system_resources()
    add_sample(lines, "system_cpu_utilization_percent", sys_res.get("cpu_percent_total"))
    for c_idx, c_pct in enumerate(sys_res.get("cpu_percent_cores", [])):
        add_sample(lines, "system_cpu_core_utilization_percent", c_pct, {"core": str(c_idx)})

    add_sample(lines, "system_memory_used_bytes", sys_res.get("ram_used"))
    add_sample(lines, "system_memory_total_bytes", sys_res.get("ram_total"))
    add_sample(lines, "system_memory_utilization_percent", sys_res.get("ram_pct"))
    add_sample(lines, "system_pagefile_used_bytes", sys_res.get("pagefile_used"))
    add_sample(lines, "system_pagefile_total_bytes", sys_res.get("pagefile_total"))
    add_sample(lines, "system_pagefile_utilization_percent", sys_res.get("pagefile_pct"))
    add_sample(lines, "system_uptime_seconds", sys_res.get("uptime_sec"))

    for d in sys_res.get("disks", []):
        mount_clean = d["mountpoint"].rstrip("\\") if d["mountpoint"] else d["mountpoint"]
        d_lbl = {"drive": mount_clean, "fstype": d["fstype"]}
        add_sample(lines, "system_disk_used_bytes", d["used"], d_lbl)
        add_sample(lines, "system_disk_total_bytes", d["total"], d_lbl)
        add_sample(lines, "system_disk_utilization_percent", d["percent"], d_lbl)

    # 5. Mạng, Cổng dịch vụ và Latency Dials đa điểm
    add_sample(
        lines,
        "net_wan_info",
        1,
        {
            "public_ipv4": slow_cache.get("public_ipv4", "None"),
            "public_ipv6": slow_cache.get("public_ipv6", "None"),
            "isp": slow_cache.get("isp", "unknown"),
        }
    )

    for p in collect_listening_ports_windows():
        add_sample(
            lines,
            "net_listening_port_info",
            1,
            {
                "port": p["port"],
                "proto": p["proto"],
                "process": p["process"],
                "exposure": p["exposure"],
            }
        )

    for tid, tinfo in slow_cache.get("latencies", {}).items():
        if tinfo.get("val") is not None:
            add_sample(
                lines,
                "net_ping_latency_ms",
                tinfo["val"],
                {
                    "target": tid,
                    "service": tinfo["service"],
                    "category": tinfo["category"],
                }
            )

    return "\n".join(lines) + "\n"


# ==============================================================================
# 7. VÒNG LẶP THU THẬP NỀN (BACKGROUND WORKER THREAD)
# ==============================================================================

def background_collector_loop():
    """Worker runs in background: fast poll thermals/watts/voltages every 5s, slow poll EventLog/Ping every 60s."""
    global metrics_output_text
    logging.info(f"Starting Background Collector Loop (Fast: {FAST_INTERVAL}s, Slow: {SLOW_INTERVAL}s)")

    while True:
        try:
            now = time.time()

            # Quá trình chậm: EventLog khám nghiệm sập nguồn & Đo ping dials
            if now - slow_cache["last_run"] >= SLOW_INTERVAL or slow_cache["last_run"] == 0:
                slow_cache["last_run"] = now

                # 1. Khám nghiệm Event Log
                crashes, last_c, is_unexp, whea_c = query_windows_crash_events()
                slow_cache["crashes"] = crashes
                slow_cache["last_crash_info"] = last_c
                slow_cache["last_reboot_unexpected"] = is_unexp
                slow_cache["whea_count"] = whea_c

                # 2. Lấy Public IP & ISP
                pub_ip, isp_name = get_public_ip_and_isp()
                slow_cache["public_ipv4"] = pub_ip
                slow_cache["isp"] = isp_name

                # 3. Đo Latency Dials đa điểm (có cơ chế giữ cache chống nhấp nháy mất dial)
                gw_ip, dns_ips = get_default_gateway_and_dns_windows()
                prev_lat = slow_cache.get("latencies", {})
                fail_counts = slow_cache.get("latency_fail_counts", {})
                new_latencies = {}

                if gw_ip:
                    gw_val = ping_target_windows(gw_ip)
                    if gw_val is not None:
                        fail_counts["gateway"] = 0
                        new_latencies["gateway"] = {"val": gw_val, "service": "Gateway", "category": "local_network"}
                    elif "gateway" in prev_lat and fail_counts.get("gateway", 0) < 3:
                        fail_counts["gateway"] = fail_counts.get("gateway", 0) + 1
                        new_latencies["gateway"] = prev_lat["gateway"]

                if dns_ips:
                    dns_val = ping_target_windows(dns_ips[0])
                    if dns_val is not None:
                        fail_counts["dns"] = 0
                        new_latencies["dns"] = {"val": dns_val, "service": "DNS", "category": "local_network"}
                    elif "dns" in prev_lat and fail_counts.get("dns", 0) < 3:
                        fail_counts["dns"] = fail_counts.get("dns", 0) + 1
                        new_latencies["dns"] = prev_lat["dns"]

                for tid, tname, thost, tcat in LATENCY_TARGETS:
                    val = ping_target_windows(thost)
                    if val is not None:
                        fail_counts[tid] = 0
                        new_latencies[tid] = {"val": val, "service": tname, "category": tcat}
                    elif tid in prev_lat and fail_counts.get(tid, 0) < 3:
                        fail_counts[tid] = fail_counts.get(tid, 0) + 1
                        new_latencies[tid] = prev_lat[tid]
                    else:
                        fail_counts[tid] = fail_counts.get(tid, 0) + 1

                slow_cache["latency_fail_counts"] = fail_counts
                slow_cache["latencies"] = new_latencies

            # Tạo text metrics hoàn chỉnh và cập nhật vào biến chia sẻ
            text = generate_prometheus_metrics()
            with state_lock:
                metrics_output_text = text

        except Exception as e:
            logging.error(f"Error in collector loop: {e}", exc_info=True)

        time.sleep(FAST_INTERVAL)


# ==============================================================================
# 8. HTTP SERVER ĐỘC LẬP XUẤT PROMETHEUS ENDPOINT (:9100/metrics)
# ==============================================================================

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/metrics", "/metrics/"]:
            with state_lock:
                content = metrics_output_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/":
            body = "<html><body><h1>Windows Unified Monitoring Agent</h1><p><a href='/metrics'>Metrics</a></p></body></html>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Tắt log access thông thường để tránh rác console/file log
        pass


def run_agent_server():
    # 1. Khởi chạy worker thread
    t = threading.Thread(target=background_collector_loop, daemon=True)
    t.start()

    # 2. Khởi chạy HTTP Server trên port 9100
    server_addr = (BIND_IP, METRICS_PORT)
    httpd = HTTPServer(server_addr, MetricsHandler)
    logging.info(f"Windows Monitoring Agent listening at http://{BIND_IP}:{METRICS_PORT}/metrics")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping Agent on request.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_agent_server()


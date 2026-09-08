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
import ctypes
import ipaddress
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Force UTF-8 on Windows console / NSSM service streams to prevent charmap/cp1252 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Import optional libraries
try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

METRICS_PORT = int(os.environ.get("AGENT_PORT", "9100"))
BIND_IP = os.environ.get("AGENT_BIND_IP", "0.0.0.0")
FAST_INTERVAL = float(os.environ.get("AGENT_FAST_INTERVAL", "5"))   # 5s: Thermals, Watts, Voltages, CPU, RAM, IO
SLOW_INTERVAL = float(os.environ.get("AGENT_SLOW_INTERVAL", "60"))  # 60s: EventLog, Ping Dials, Inventory, Disks

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
    "gateway_ip": "",
    "gateway_mac": "-",
    "gateway_iface": "Ethernet",
    "dns_servers": [],
    "double_nat": 0,
    "cgnat": 0,
    "symmetric": 0,
    "tailscale_online": 0,
    "latencies": {},
    "latency_fail_counts": {},
    "device_inventory": [],
    "disk_temperatures": [],
    "firewall_settings": {},
}


# ==============================================================================
# 1. POST-MORTEM CRASH ENGINE (EVENT LOG ID 41 & BUGCHECK)
# ==============================================================================

def parse_bugcheck_code(code_val):
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
    crashes = []
    last_crash = {}
    is_unexpected_boot = 0
    whea_count = 0

    ps_script = """
    $events = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41} -MaxEvents 10 -ErrorAction SilentlyContinue
    if ($events) {
        $events | ForEach-Object {
            $xml = [xml]$_.ToXml()
            $eventData = @{}
            $xml.Event.EventData.Data | ForEach-Object { $eventData[$_.Name] = $_.'#text' }
            [PSCustomObject]@{
                TimeCreated = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
                Epoch = [int64]($_.TimeCreated.ToUniversalTime() - [datetime]'1970-01-01').TotalSeconds
                BugcheckCode = if ($eventData['BugcheckCode']) { $eventData['BugcheckCode'] } else { '0' }
                BugcheckParam1 = if ($eventData['BugcheckParameter1']) { $eventData['BugcheckParameter1'] } else { '0x0' }
                PowerButtonTimestamp = if ($eventData['PowerButtonTimestamp']) { $eventData['PowerButtonTimestamp'] } else { '0' }
            }
        } | ConvertTo-Json -Compress
    } else { '[]' }
    """

    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                             capture_output=True, text=True, timeout=8)
        if out.returncode == 0 and out.stdout.strip():
            raw_text = out.stdout.strip()
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
                boot_time = psutil.boot_time() if psutil else 0
                if abs(last_crash["epoch"] - boot_time) < 1800:
                    is_unexpected_boot = 1
    except Exception as e:
        logging.debug(f"Error querying Kernel-Power 41: {e}")

    try:
        whea_script = """
        (Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=(Get-Date).AddHours(-24)} -ErrorAction SilentlyContinue).Count
        """
        w_out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", whea_script],
                               capture_output=True, text=True, timeout=5)
        if w_out.returncode == 0 and w_out.stdout.strip().isdigit():
            whea_count = int(w_out.stdout.strip())
    except Exception:
        pass

    return crashes, last_crash, is_unexpected_boot, whea_count


# ==============================================================================
# 2. NVIDIA GPU METRICS (NVAPI, NVML FIELD VALUES, PYNVML, NVIDIA-SMI)
# ==============================================================================

_nvml_initialized = False

def init_nvml():
    global _nvml_initialized
    if _nvml_initialized:
        return True
    if not pynvml:
        return False
    try:
        pynvml.nvmlInit()
        _nvml_initialized = True
        return True
    except Exception as e:
        logging.debug(f"Could not initialize NVML: {e}")
        return False


class _NvSensor(ctypes.Structure):
    _fields_ = [
        ("controller", ctypes.c_int),
        ("defaultMinTemp", ctypes.c_int),
        ("defaultMaxTemp", ctypes.c_int),
        ("currentTemp", ctypes.c_int),
        ("target", ctypes.c_int),
    ]

class _NvThermalSettings(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("sensor", _NvSensor * 3), # Official NVAPI MAX_SENSORS_PER_GPU is 3 (68 bytes total)
    ]

def query_nvapi_thermals():
    res = {}
    if sys.platform != "win32":
        return res
    try:
        nvapi = ctypes.windll.LoadLibrary("nvapi64.dll")
        nvapi.nvapi_QueryInterface.restype = ctypes.c_void_p
        nvapi.nvapi_QueryInterface.argtypes = [ctypes.c_uint32]

        init_ptr = nvapi.nvapi_QueryInterface(0x0150E828) # NvAPI_Initialize
        if not init_ptr:
            return res
        NvAPI_Initialize = ctypes.WINFUNCTYPE(ctypes.c_int)(init_ptr)
        if NvAPI_Initialize() != 0:
            return res

        enum_ptr = nvapi.nvapi_QueryInterface(0xE3640561) # NvAPI_EnumPhysicalGPUs
        if not enum_ptr:
            return res
        NvAPI_EnumPhysicalGPUs = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(enum_ptr)

        gpu_handles = (ctypes.c_void_p * 64)()
        gpu_count = ctypes.c_int(0)
        if NvAPI_EnumPhysicalGPUs(gpu_handles, ctypes.byref(gpu_count)) != 0 or gpu_count.value <= 0:
            return res

        therm_ptr = nvapi.nvapi_QueryInterface(0xE4C63B40) # NvAPI_GPU_GetThermalSettings
        if not therm_ptr:
            return res
        NvAPI_GPU_GetThermalSettings = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_NvThermalSettings)
        )(therm_ptr)

        for i in range(gpu_count.value):
            h = gpu_handles[i]
            found = False
            # Try official V2 (0x20044) then V1 (0x10044)
            for ver_code in (0x20044, 0x10044):
                if found:
                    break
                for s_target in (0, 15):
                    settings = _NvThermalSettings()
                    settings.version = ver_code
                    settings.count = 0
                    if NvAPI_GPU_GetThermalSettings(h, s_target, ctypes.byref(settings)) == 0 and settings.count > 0:
                        core = None
                        hotspot = None
                        vram = None
                        for s_idx in range(min(settings.count, 3)):
                            s = settings.sensor[s_idx]
                            t_val = float(s.currentTemp)
                            if 0 < t_val < 130:
                                if s.target == 1 and core is None:
                                    core = t_val
                                elif s.target == 2 and vram is None:
                                    vram = t_val
                                elif s.target in (3, 8, 9) and hotspot is None:
                                    hotspot = t_val
                                elif s_idx == 1 and hotspot is None:
                                    hotspot = t_val
                                elif s_idx == 2 and vram is None:
                                    vram = t_val
                        if core or hotspot or vram:
                            res[i] = {"core": core, "hotspot": hotspot, "vram": vram}
                            found = True
                            break
    except Exception as e:
        logging.debug(f"NVAPI thermals error: {e}")
    return res


def collect_nvidia_gpu_metrics(lhm_hardware=None):
    gpus = []

    # 1. Native pynvml
    if init_nvml():
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            for idx in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")

                # Core Temp
                try:
                    temp_core = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
                except Exception:
                    temp_core = None

                temp_hotspot = None
                temp_mem = None

                # Try NVML Field Values for Hotspot & Memory (Driver 510+)
                try:
                    class _NvmlFieldValue(ctypes.Structure):
                        _fields_ = [
                            ("fieldId", ctypes.c_uint32),
                            ("scopeId", ctypes.c_uint32),
                            ("timestamp", ctypes.c_int64),
                            ("latencyUsec", ctypes.c_int64),
                            ("valueType", ctypes.c_int32),
                            ("nvmlReturn", ctypes.c_int32),
                            ("value", ctypes.c_int64),
                        ]
                    fields = (_NvmlFieldValue * 2)()
                    fields[0].fieldId = 74 # NVML_FI_DEV_MEMORY_TEMP
                    fields[1].fieldId = 75 # NVML_FI_DEV_HOTSPOT_TEMP
                    if hasattr(pynvml, "nvmlDeviceGetFieldValues"):
                        ret = pynvml.nvmlDeviceGetFieldValues(handle, 2, ctypes.byref(fields))
                        if ret == 0:
                            if fields[0].nvmlReturn == 0 and 0 < fields[0].value < 130:
                                temp_mem = float(fields[0].value)
                            if fields[1].nvmlReturn == 0 and 0 < fields[1].value < 130:
                                temp_hotspot = float(fields[1].value)
                except Exception:
                    pass

                # Power
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

                fan_pct = None
                try:
                    fan_pct = float(pynvml.nvmlDeviceGetFanSpeed(handle))
                except Exception:
                    pass

                util_gpu = None
                try:
                    rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    util_gpu = float(rates.gpu)
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

    # 2. NVAPI Direct Query for Hotspot / VRAM
    nvapi_map = query_nvapi_thermals()
    for idx, g in enumerate(gpus):
        nv = nvapi_map.get(idx, {})
        if g.get("temp_hotspot") is None and nv.get("hotspot") is not None:
            g["temp_hotspot"] = nv["hotspot"]
        if g.get("temp_mem") is None and nv.get("vram") is not None:
            g["temp_mem"] = nv["vram"]
        if g.get("temp_core") is None and nv.get("core") is not None:
            g["temp_core"] = nv["core"]

    # 3. Fallback to LibreHardwareMonitor for GPU Hotspot/VRAM if still None
    if lhm_hardware:
        for g in gpus:
            if g.get("temp_hotspot") is None and lhm_hardware.get("gpu_hotspot") is not None:
                g["temp_hotspot"] = lhm_hardware["gpu_hotspot"]
            if g.get("temp_mem") is None and lhm_hardware.get("gpu_vram") is not None:
                g["temp_mem"] = lhm_hardware["gpu_vram"]

    # 4. Fallback to nvidia-smi if NVML completely failed
    if not gpus:
        smi_data = query_nvidia_smi()
        if smi_data:
            gpus = smi_data
            for idx, g in enumerate(gpus):
                nv = nvapi_map.get(idx, {})
                if nv.get("hotspot") is not None:
                    g["temp_hotspot"] = nv["hotspot"]
                if nv.get("vram") is not None:
                    g["temp_mem"] = nv["vram"]
                if lhm_hardware:
                    if g.get("temp_hotspot") is None and lhm_hardware.get("gpu_hotspot"):
                        g["temp_hotspot"] = lhm_hardware["gpu_hotspot"]
                    if g.get("temp_mem") is None and lhm_hardware.get("gpu_vram"):
                        g["temp_mem"] = lhm_hardware["gpu_vram"]

    # Calculate Hotspot Delta (Hotspot - Core)
    for g in gpus:
        if g.get("temp_core") is not None and g.get("temp_hotspot") is not None:
            g["hotspot_delta"] = round(g["temp_hotspot"] - g["temp_core"], 2)
        else:
            g["hotspot_delta"] = 0.0

    return gpus


def query_nvidia_smi():
    results = []
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
# 3. PSU VOLTAGES (12V, 5V, 3.3V), CPU POWER & PHYSICAL DISK THERMALS
# ==============================================================================

def collect_motherboard_and_cpu_power():
    data = {
        "v12": None,
        "v5": None,
        "v33": None,
        "cpu_power_w": None,
        "cpu_temp_package": None,
        "gpu_hotspot": None,
        "gpu_vram": None,
        "nvme_temps": {},
    }

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lhm_dll = os.path.join(base_dir, "bin", "lhm", "LibreHardwareMonitorLib.dll")
    ohm_dll = os.path.join(base_dir, "bin", "lhm", "OpenHardwareMonitorLib.dll")

    # Method 1: In-process assembly load via PowerShell (Direct ring-0 SuperIO access)
    if os.path.exists(lhm_dll):
        ps_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"""
            try {{
                Add-Type -Path '{lhm_dll}' -ErrorAction Stop
                $c = New-Object LibreHardwareMonitor.Hardware.Computer
                $c.IsCpuEnabled = $true; $c.IsGpuEnabled = $true; $c.IsMotherboardEnabled = $true; $c.IsStorageEnabled = $true
                $c.Open()
                $list = @()
                foreach ($h in $c.Hardware) {{
                    $h.Update()
                    foreach ($sub in $h.SubHardware) {{ $sub.Update() }}
                    foreach ($s in $h.Sensors) {{
                        $list += [PSCustomObject]@{{ Name = $s.Name; SensorType = $s.SensorType.ToString(); Value = $s.Value }}
                    }}
                }}
                $c.Close()
                $list | ConvertTo-Json -Compress
            }} catch {{ '[]' }}
            """
        ]
    elif os.path.exists(ohm_dll):
        ps_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"""
            try {{
                Add-Type -Path '{ohm_dll}' -ErrorAction Stop
                $c = New-Object OpenHardwareMonitor.Hardware.Computer
                $c.IsCpuEnabled = $true; $c.IsGpuEnabled = $true; $c.IsMotherboardEnabled = $true; $c.IsStorageEnabled = $true
                $c.Open()
                $list = @()
                foreach ($h in $c.Hardware) {{
                    $h.Update()
                    foreach ($sub in $h.SubHardware) {{ $sub.Update() }}
                    foreach ($s in $h.Sensors) {{
                        $list += [PSCustomObject]@{{ Name = $s.Name; SensorType = $s.SensorType.ToString(); Value = $s.Value }}
                    }}
                }}
                $c.Close()
                $list | ConvertTo-Json -Compress
            }} catch {{ '[]' }}
            """
        ]
    else:
        # Method 2: Fallback to WMI
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
        out = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=6)
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

                elif stype == "power":
                    if "cpu package" in name or "package" in name or "cpu total" in name:
                        data["cpu_power_w"] = round(fval, 2)

                elif stype == "temperature":
                    if "cpu package" in name or "package" in name or "core max" in name:
                        data["cpu_temp_package"] = round(fval, 1)
                    elif "hot spot" in name or "hotspot" in name:
                        data["gpu_hotspot"] = round(fval, 1)
                    elif "gpu memory" in name or "vram" in name or "memory junction" in name:
                        data["gpu_vram"] = round(fval, 1)
                    elif "nvme" in name or "ssd" in name or "drive" in name:
                        data["nvme_temps"][s.get("Name", "NVMe")] = round(fval, 1)
    except Exception:
        pass

    if data["cpu_temp_package"] is None:
        try:
            acpi_cmd = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "(Get-CimInstance -Namespace 'root\\wmi' -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue).CurrentTemperature"
            ]
            acpi_out = subprocess.run(acpi_cmd, capture_output=True, text=True, timeout=4)
            if acpi_out.returncode == 0 and acpi_out.stdout.strip().isdigit():
                kelvin_tenths = float(acpi_out.stdout.strip())
                celsius = round((kelvin_tenths / 10.0) - 273.15, 1)
                if 0 < celsius < 125:
                    data["cpu_temp_package"] = celsius
        except Exception:
            pass

    return data


def collect_windows_disk_temperatures():
    disks = []
    ps_cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        """
        Get-PhysicalDisk | ForEach-Object {
            $d = $_
            $rel = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
            $temp = if ($rel -and $rel.Temperature) { $rel.Temperature } else { $null }
            [PSCustomObject]@{
                FriendlyName = $d.FriendlyName
                MediaType = $d.MediaType
                BusType = $d.BusType
                DeviceId = $d.DeviceId
                Temperature = $temp
            }
        } | ConvertTo-Json -Compress
        """
    ]
    try:
        out = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=6)
        if out.returncode == 0 and out.stdout.strip():
            raw = out.stdout.strip()
            items = [json.loads(raw)] if raw.startswith("{") else json.loads(raw)
            for it in items:
                temp = it.get("Temperature")
                if temp is not None:
                    try:
                        ftemp = float(temp)
                        if 0 <= ftemp <= 125:
                            disks.append({
                                "name": it.get("FriendlyName", f"Disk {it.get('DeviceId', 0)}"),
                                "media": it.get("MediaType", "SSD"),
                                "bus": it.get("BusType", "NVMe"),
                                "temp": ftemp,
                            })
                    except Exception:
                        pass
    except Exception:
        pass
    return disks


# ==============================================================================
# 4. SYSTEM RESOURCES (CPU, RAM, DISK IO, NET IO, UPTIME)
# ==============================================================================

def collect_system_resources():
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
        "disk_read_bytes_total": 0,
        "disk_written_bytes_total": 0,
        "disk_reads_completed_total": 0,
        "disk_writes_completed_total": 0,
        "net_bytes_recv_total": 0,
        "net_bytes_sent_total": 0,
        "net_drop_in_total": 0,
        "net_drop_out_total": 0,
        "net_err_in_total": 0,
        "net_err_out_total": 0,
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

            dio = psutil.disk_io_counters()
            if dio:
                data["disk_read_bytes_total"] = dio.read_bytes
                data["disk_written_bytes_total"] = dio.write_bytes
                data["disk_reads_completed_total"] = dio.read_count
                data["disk_writes_completed_total"] = dio.write_count

            nio = psutil.net_io_counters()
            if nio:
                data["net_bytes_recv_total"] = nio.bytes_recv
                data["net_bytes_sent_total"] = nio.bytes_sent
                data["net_drop_in_total"] = nio.dropin
                data["net_drop_out_total"] = nio.dropout
                data["net_err_in_total"] = nio.errin
                data["net_err_out_total"] = nio.errout

        except Exception as e:
            logging.debug(f"psutil error: {e}")

    return data


# ==============================================================================
# 5. NETWORK METRICS, TOPOLOGY, PORTS, CONNECTIONS & INVENTORY
# ==============================================================================

def ping_target_windows(target_host):
    if not target_host:
        return None
    hosts = [target_host] if isinstance(target_host, str) else list(target_host)
    for h in hosts:
        try:
            out = subprocess.run(
                ["ping", "-4", "-n", "2", "-w", "2000", str(h)],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if out.returncode == 0 and out.stdout:
                matches = re.findall(r"time[<=]([0-9.]+)\s*ms", out.stdout, re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"Minimum\s*=\s*([0-9.]+)\s*ms", out.stdout, re.IGNORECASE)
                if matches:
                    return min(float(m) for m in matches)
        except Exception:
            pass
    return None


def get_default_gateway_and_dns_windows():
    gw = None
    gw_iface = "Ethernet"
    dns_list = []
    try:
        out = subprocess.run(["ipconfig", "/all"], capture_output=True, text=True, timeout=4)
        if out.returncode == 0:
            current_adapter = "Ethernet"
            for line in out.stdout.splitlines():
                line = line.strip()
                if "adapter" in line.lower() and ":" in line:
                    current_adapter = line.split("adapter")[-1].replace(":", "").strip()
                elif "Default Gateway" in line or "Cổng mặc định" in line:
                    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                    if m and m.group(1) != "0.0.0.0" and not gw:
                        gw = m.group(1)
                        gw_iface = current_adapter
                elif "DNS Servers" in line or "Máy chủ DNS" in line:
                    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                    if m and m.group(1) not in dns_list:
                        dns_list.append(m.group(1))
    except Exception:
        pass
    return gw, gw_iface, dns_list


def collect_listening_ports_windows():
    ports = []
    try:
        conns = psutil.net_connections(kind="inet") if psutil else []
        seen = set()
        for c in conns:
            if c.status == "LISTEN" or (c.type == socket.SOCK_DGRAM and c.laddr):
                p_num = c.laddr.port
                p_ip = c.laddr.ip
                proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
                key = (p_num, proto)
                if key in seen:
                    continue
                seen.add(key)

                proc_name = "unknown"
                try:
                    if c.pid:
                        proc = psutil.Process(c.pid)
                        proc_name = proc.name()
                except Exception:
                    pass

                exposure = "Localhost" if p_ip.startswith("127.") or p_ip == "::1" else "Public / LAN"
                ports.append({
                    "port": str(p_num),
                    "proto": proto,
                    "process": proc_name,
                    "exposure": exposure,
                })
    except Exception:
        pass
    return ports


def collect_tcp_states_and_active():
    states = {}
    active_conns = []
    try:
        conns = psutil.net_connections(kind="inet") if psutil else []
        for c in conns:
            st = c.status or "UNKNOWN"
            states[st] = states.get(st, 0) + 1
            if c.status == "ESTABLISHED" and c.raddr:
                proc_name = "-"
                try:
                    if c.pid:
                        p = psutil.Process(c.pid)
                        proc_name = p.name()
                except Exception:
                    pass
                local_str = f"{c.laddr.ip}:{c.laddr.port}"
                remote_str = f"{c.raddr.ip}:{c.raddr.port}"
                active_conns.append({
                    "local": local_str,
                    "remote": remote_str,
                    "process": proc_name,
                    "proto": "tcp"
                })
    except Exception:
        pass
    return states, active_conns[:25]


def collect_network_interfaces():
    interfaces = []
    try:
        if psutil:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for iface_name, addr_list in addrs.items():
                ipv4 = ""
                mac = ""
                for a in addr_list:
                    if a.family == socket.AF_INET:
                        ipv4 = a.address
                    elif hasattr(psutil, "AF_LINK") and a.family == psutil.AF_LINK:
                        mac = a.address
                if not ipv4 and not mac:
                    continue
                st = stats.get(iface_name)
                is_up = "up" if st and st.isup else "down"
                speed = str(st.speed) if st and st.speed > 0 else "auto"
                net_type = "Wireless" if "wi-fi" in iface_name.lower() or "wlan" in iface_name.lower() else (
                    "Tailscale" if "tailscale" in iface_name.lower() else "Ethernet"
                )
                interfaces.append({
                    "interface": iface_name,
                    "ip": ipv4 or "-",
                    "mac": mac or "-",
                    "status": is_up,
                    "speed_mbps": speed,
                    "net_type": net_type
                })
    except Exception:
        pass
    return interfaces


def collect_device_inventory():
    devices = []
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=4)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and "-" in parts[1]:
                    ip = parts[0]
                    mac = parts[1]
                    typ = parts[2]
                    if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255") or ip == "255.255.255.255":
                        continue
                    devices.append({
                        "hostname": ip,
                        "ip": ip,
                        "mac": mac,
                        "network": "LAN",
                        "status": "reachable" if typ == "dynamic" else "static",
                        "os": "unknown",
                        "latency": "local"
                    })
    except Exception:
        pass

    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=4)
        if out.returncode == 0 and out.stdout.strip():
            ts_data = json.loads(out.stdout)
            peers = ts_data.get("Peer", {})
            for pid, peer in peers.items():
                host = peer.get("HostName", "unknown")
                ts_ips = peer.get("TailscaleIPs", [])
                ip = ts_ips[0] if ts_ips else "-"
                online = peer.get("Online", False)
                os_name = peer.get("OS", "unknown")
                devices.append({
                    "hostname": host,
                    "ip": ip,
                    "mac": "-",
                    "network": "Tailscale",
                    "status": "online" if online else "offline",
                    "os": os_name,
                    "latency": "mesh"
                })
    except Exception:
        pass

    return devices


def collect_windows_firewall_settings():
    settings = {}
    try:
        ps_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-NetFirewallProfile | Select-Object Name, Enabled) | ConvertTo-Json -Compress"
        ]
        out = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=4)
        if out.returncode == 0 and out.stdout.strip():
            raw = out.stdout.strip()
            items = [json.loads(raw)] if raw.startswith("{") else json.loads(raw)
            for it in items:
                name = it.get("Name", "").lower()
                enabled = 1 if it.get("Enabled") is True else 0
                settings[f"firewall_profile_{name}"] = enabled
    except Exception:
        pass
    return settings


def get_public_ip_and_isp():
    ipv4 = "None"
    isp = "unknown"
    try:
        out = subprocess.run(["curl.exe", "-s", "--max-time", "4", "https://ipinfo.io/json"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            ipv4 = data.get("ip", "None")
            org = data.get("org", "unknown")
            isp = re.sub(r"^AS\d+\s*", "", org)
    except Exception:
        pass
    return ipv4, isp


def detect_cgnat(pub_ip):
    if not pub_ip or pub_ip == "None":
        return 0, 0
    try:
        ip_obj = ipaddress.ip_address(pub_ip)
        cgnat_net = ipaddress.ip_network("100.64.0.0/10")
        is_cgnat = 1 if ip_obj in cgnat_net else 0
        is_private = 1 if ip_obj.is_private else 0
        return is_cgnat, is_private
    except Exception:
        return 0, 0


# ==============================================================================
# 6. PROMETHEUS EXPOSITION GENERATOR
# ==============================================================================

def escape_label_value(val):
    if val is None:
        return ""
    return str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def add_sample(lines, name, value, labels=None):
    if value is None:
        return
    if labels:
        lbl_str = ",".join(f'{k}="{escape_label_value(v)}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{lbl_str}}} {value}")
    else:
        lines.append(f"{name} {value}")


def generate_prometheus_metrics():
    lines = []
    lines.append("# Windows Unified Monitoring Agent & Blackbox Flight Recorder")

    # 1. Crash Diagnostics
    unexp = slow_cache.get("last_reboot_unexpected", 0)
    add_sample(lines, "windows_last_reboot_unexpected", unexp)
    add_sample(lines, "windows_unexpected_boot_flag", unexp)
    whea_c = slow_cache.get("whea_count", 0)
    add_sample(lines, "windows_whea_errors_total", whea_c)
    add_sample(lines, "windows_whea_errors_24h_total", whea_c)

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

    # 2. PSU Voltages, CPU Power & Physical Disks
    hw = collect_motherboard_and_cpu_power()
    if hw.get("v12") is not None:
        add_sample(lines, "motherboard_voltage_12v", hw["v12"])
        add_sample(lines, "motherboard_voltage_volts", hw["v12"], {"rail": "12v"})
    if hw.get("v5") is not None:
        add_sample(lines, "motherboard_voltage_5v", hw["v5"])
        add_sample(lines, "motherboard_voltage_volts", hw["v5"], {"rail": "5v"})
    if hw.get("v33") is not None:
        add_sample(lines, "motherboard_voltage_3v3", hw["v33"])
        add_sample(lines, "motherboard_voltage_volts", hw["v33"], {"rail": "3.3v"})
    if hw.get("cpu_power_w") is not None:
        add_sample(lines, "cpu_package_power_watts", hw["cpu_power_w"])
    if hw.get("cpu_temp_package") is not None:
        add_sample(lines, "cpu_package_temp_celsius", hw["cpu_temp_package"])
        add_sample(lines, "cpu_package_temperature_celsius", hw["cpu_temp_package"])
        add_sample(lines, "thermal_temperature_celsius", hw["cpu_temp_package"], {"component": "cpu", "sensor": "package"})

    # 3. NVIDIA GPU Metrics (with NVAPI, NVML field values, LHM fallback)
    gpus = collect_nvidia_gpu_metrics(lhm_hardware=hw)
    for g in gpus:
        lbl = {"gpu": g["index"], "name": g["name"]}
        # Core
        add_sample(lines, "nvidia_gpu_temp_celsius", g.get("temp_core"), lbl)
        add_sample(lines, "gpu_temperature_celsius", g.get("temp_core"), lbl)
        # Hotspot
        add_sample(lines, "nvidia_gpu_hotspot_temp_celsius", g.get("temp_hotspot"), lbl)
        add_sample(lines, "gpu_hotspot_temperature_celsius", g.get("temp_hotspot"), lbl)
        # VRAM / Memory
        add_sample(lines, "nvidia_gpu_vram_temp_celsius", g.get("temp_mem"), lbl)
        add_sample(lines, "gpu_memory_temperature_celsius", g.get("temp_mem"), lbl)
        # Delta
        add_sample(lines, "nvidia_gpu_hotspot_delta_celsius", g.get("hotspot_delta"), lbl)
        add_sample(lines, "gpu_hotspot_delta_celsius", g.get("hotspot_delta"), lbl)
        # Power
        add_sample(lines, "nvidia_gpu_power_watts", g.get("power_w"), lbl)
        add_sample(lines, "gpu_power_draw_watts", g.get("power_w"), lbl)
        add_sample(lines, "nvidia_gpu_power_limit_watts", g.get("power_limit_w"), lbl)
        add_sample(lines, "gpu_power_limit_watts", g.get("power_limit_w"), lbl)
        # Fan
        add_sample(lines, "nvidia_gpu_fan_speed_percent", g.get("fan_pct"), lbl)
        add_sample(lines, "gpu_fan_speed_percent", g.get("fan_pct"), lbl)
        # Utilization & Clocks
        add_sample(lines, "nvidia_gpu_utilization_percent", g.get("util_gpu"), lbl)
        add_sample(lines, "nvidia_gpu_memory_used_bytes", g.get("mem_used"), lbl)
        add_sample(lines, "nvidia_gpu_memory_total_bytes", g.get("mem_total"), lbl)
        add_sample(lines, "nvidia_gpu_clock_graphics_mhz", g.get("clock_core"), lbl)
        add_sample(lines, "nvidia_gpu_clock_memory_mhz", g.get("clock_mem"), lbl)

        # Standard thermal_temperature_celsius
        add_sample(lines, "thermal_temperature_celsius", g.get("temp_core"), {"component": "gpu", "sensor": "core"})
        if g.get("temp_hotspot"):
            add_sample(lines, "thermal_temperature_celsius", g.get("temp_hotspot"), {"component": "gpu", "sensor": "hotspot"})
        if g.get("temp_mem"):
            add_sample(lines, "thermal_temperature_celsius", g.get("temp_mem"), {"component": "gpu", "sensor": "vram"})
        if g.get("fan_pct"):
            add_sample(lines, "thermal_fan_speed_percent", g.get("fan_pct"), {"component": "fan", "sensor": "gpu"})
        if g.get("power_w"):
            add_sample(lines, "thermal_gpu_power_watts", g.get("power_w"))

    # Physical Disks Deduplication (prevent duplicate dials on dashboard)
    seen_disks = set()
    for d in slow_cache.get("disk_temperatures", []):
        d_name = d["name"]
        if d_name in seen_disks:
            continue
        seen_disks.add(d_name)
        add_sample(lines, "system_disk_temperature_celsius", d["temp"], {"disk": d_name, "media": d["media"], "bus": d["bus"]})
        add_sample(lines, "nvme_temperature_celsius", d["temp"], {"disk": d_name})
        c_type = "nvme" if "nvme" in d["bus"].lower() or "nvme" in d_name.lower() else "disk"
        add_sample(lines, "thermal_temperature_celsius", d["temp"], {"component": c_type, "sensor": d_name})

    for nv_name, nv_temp in hw.get("nvme_temps", {}).items():
        if nv_name not in seen_disks:
            seen_disks.add(nv_name)
            add_sample(lines, "nvme_temperature_celsius", nv_temp, {"disk": nv_name})
            add_sample(lines, "thermal_temperature_celsius", nv_temp, {"component": "nvme", "sensor": nv_name})

    # 4. System Resources (CPU, RAM, Pagefile, Disks, IO)
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

    # Disk IO
    add_sample(lines, "system_disk_read_bytes_total", sys_res.get("disk_read_bytes_total"))
    add_sample(lines, "system_disk_written_bytes_total", sys_res.get("disk_written_bytes_total"))
    add_sample(lines, "system_disk_reads_completed_total", sys_res.get("disk_reads_completed_total"))
    add_sample(lines, "system_disk_writes_completed_total", sys_res.get("disk_writes_completed_total"))

    # Network IO
    add_sample(lines, "system_network_receive_bytes_total", sys_res.get("net_bytes_recv_total"))
    add_sample(lines, "system_network_transmit_bytes_total", sys_res.get("net_bytes_sent_total"))
    add_sample(lines, "system_network_receive_drop_total", sys_res.get("net_drop_in_total"))
    add_sample(lines, "system_network_transmit_drop_total", sys_res.get("net_drop_out_total"))
    add_sample(lines, "system_network_receive_errs_total", sys_res.get("net_err_in_total"))
    add_sample(lines, "system_network_transmit_errs_total", sys_res.get("net_err_out_total"))

    for d in sys_res.get("disks", []):
        mount_clean = d["mountpoint"].rstrip("\\") if d["mountpoint"] else d["mountpoint"]
        d_lbl = {"drive": mount_clean, "fstype": d["fstype"]}
        add_sample(lines, "system_disk_used_bytes", d["used"], d_lbl)
        add_sample(lines, "system_disk_total_bytes", d["total"], d_lbl)
        add_sample(lines, "system_disk_utilization_percent", d["percent"], d_lbl)

    # 5. Network & Security Topology
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

    add_sample(lines, "net_nat_is_cgnat", slow_cache.get("cgnat", 0))
    add_sample(lines, "net_nat_is_double_nat", slow_cache.get("double_nat", 0))
    add_sample(lines, "net_nat_is_symmetric", slow_cache.get("symmetric", 0))
    add_sample(lines, "net_tailscale_status", 1 if slow_cache.get("tailscale_online", 0) else 0)
    if slow_cache.get("gateway_ip"):
        gw_ip = slow_cache["gateway_ip"]
        gw_mac = slow_cache.get("gateway_mac", "-")
        gw_iface = slow_cache.get("gateway_iface", "Ethernet")
        add_sample(lines, "net_gateway_info", 1, {
            "gateway": gw_ip,
            "gateway_ip": gw_ip,
            "gateway_mac": gw_mac,
            "interface": gw_iface,
        })

    # TCP States
    tcp_states, active_conns = collect_tcp_states_and_active()
    for st_name, count in tcp_states.items():
        add_sample(lines, "system_tcp_connections", count, {"state": st_name})

    for conn in active_conns:
        add_sample(lines, "net_active_connection_info", 1, {
            "local_addr": conn["local"],
            "remote_addr": conn["remote"],
            "process": conn["process"],
            "proto": conn["proto"],
        })

    # Interfaces
    for iface in collect_network_interfaces():
        add_sample(lines, "net_interface_info", 1, {
            "interface": iface["interface"],
            "ip": iface["ip"],
            "mac": iface["mac"],
            "status": iface["status"],
            "speed_mbps": iface["speed_mbps"],
            "net_type": iface["net_type"],
        })

    # Device Inventory
    for dev in slow_cache.get("device_inventory", []):
        add_sample(lines, "net_device_info", 1, {
            "hostname": dev["hostname"],
            "ip": dev["ip"],
            "mac": dev["mac"],
            "network": dev["network"],
            "status": dev["status"],
            "os": dev["os"],
            "latency": dev["latency"],
        })

    # Firewall settings
    for s_name, s_val in slow_cache.get("firewall_settings", {}).items():
        add_sample(lines, "net_security_setting", s_val, {"setting": s_name})

    # Listening Ports
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

    # Latency Dials
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
# 7. BACKGROUND COLLECTOR LOOP
# ==============================================================================

def background_collector_loop():
    global metrics_output_text
    logging.info(f"Starting Background Collector Loop (Fast: {FAST_INTERVAL}s, Slow: {SLOW_INTERVAL}s)")

    while True:
        try:
            now = time.time()

            if now - slow_cache["last_run"] >= SLOW_INTERVAL or slow_cache["last_run"] == 0:
                slow_cache["last_run"] = now

                # 1. Event Log
                crashes, last_c, is_unexp, whea_c = query_windows_crash_events()
                slow_cache["crashes"] = crashes
                slow_cache["last_crash_info"] = last_c
                slow_cache["last_reboot_unexpected"] = is_unexp
                slow_cache["whea_count"] = whea_c

                # 2. Public IP & ISP & CGNAT
                pub_ip, isp_name = get_public_ip_and_isp()
                slow_cache["public_ipv4"] = pub_ip
                slow_cache["isp"] = isp_name
                is_cg, is_priv = detect_cgnat(pub_ip)
                slow_cache["cgnat"] = is_cg
                slow_cache["double_nat"] = is_priv

                # 3. Device Inventory (LAN + Tailscale)
                device_inv = collect_device_inventory()
                slow_cache["device_inventory"] = device_inv
                slow_cache["tailscale_online"] = 1 if any(d["network"] == "Tailscale" for d in device_inv) else 0

                # 4. Gateway & DNS & Gateway MAC
                gw_ip, gw_iface, dns_ips = get_default_gateway_and_dns_windows()
                slow_cache["gateway_ip"] = gw_ip or ""
                slow_cache["gateway_iface"] = gw_iface or "Ethernet"
                slow_cache["dns_servers"] = dns_ips

                gw_mac = "-"
                if gw_ip:
                    for d in device_inv:
                        if d.get("ip") == gw_ip and d.get("mac") != "-":
                            gw_mac = d["mac"]
                            break
                slow_cache["gateway_mac"] = gw_mac

                # 5. Physical Disks (NVMe / SSD)
                slow_cache["disk_temperatures"] = collect_windows_disk_temperatures()

                # 6. Firewall settings
                slow_cache["firewall_settings"] = collect_windows_firewall_settings()

                # 7. Ping Latency Dials
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

            text = generate_prometheus_metrics()
            with state_lock:
                metrics_output_text = text

        except Exception as e:
            logging.error(f"Error in collector loop: {e}", exc_info=True)

        time.sleep(FAST_INTERVAL)


# ==============================================================================
# 8. HTTP SERVER (:9100/metrics)
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
        pass


def run_agent_server():
    t = threading.Thread(target=background_collector_loop, daemon=True)
    t.start()

    server_address = (BIND_IP, METRICS_PORT)
    httpd = HTTPServer(server_address, MetricsHandler)
    logging.info(f"Windows Unified Monitoring Agent started on http://{BIND_IP}:{METRICS_PORT}/metrics")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        logging.info("Agent HTTP server stopped.")


if __name__ == "__main__":
    run_agent_server()

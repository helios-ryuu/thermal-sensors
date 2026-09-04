#!/usr/bin/env python3
"""
Bộ thu thập dữ liệu Mạng & Bảo mật (Network & Security Collector)
Dành cho Ubuntu iMac trong hệ thống thermal-sensors stack.
Thu thập:
- Topology mạng (Interfaces, Subnets, Gateway, DNS)
- NAT & CGNAT & Double NAT & Symmetric NAT detection
- Danh sách thiết bị hợp nhất (Unified Device Inventory: LAN + Tailscale)
- Cổng đang mở lắng nghe & Các kết nối hoạt động
- Quy tắc tường lửa iptables & Cấu hình an ninh kernel sysctl
- Chất lượng đường truyền (Ping latency)
"""

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

OUTPUT = os.environ.get("METRICS_OUTPUT_PATH", "/textfile/network_metrics.prom")
TAILSCALE_SOCK = os.environ.get("TAILSCALE_SOCKET_PATH", "/var/run/tailscale/tailscaled.sock")
FAST_INTERVAL = float(os.environ.get("COLLECTOR_FAST_INTERVAL", "15"))
SLOW_INTERVAL = float(os.environ.get("COLLECTOR_SLOW_INTERVAL", "120"))

# Cache cho các phép kiểm tra chậm (NAT, STUN, traceroute)
slow_cache = {
    "last_run": 0.0,
    "cgnat": 0,
    "symmetric": 0,
    "double_nat": 0,
    "hairpinning": 0,
    "public_ipv4": "unknown",
    "public_ipv6": "none",
    "isp": "unknown",
    "ping_gateway": None,
    "ping_dns": None,
    "ping_internet": None,
    "ping_derp": None,
}


def prometheus_escape(value):
    """Thoát các ký tự đặc biệt theo chuẩn Prometheus label value."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def add_sample(lines, metric, value, labels=None):
    """Thêm một dòng metric Prometheus."""
    label_str = ""
    if labels:
        label_str = "{" + ",".join(
            f'{k}="{prometheus_escape(v)}"' for k, v in sorted(labels.items())
        ) + "}"
    lines.append(f"{metric}{label_str} {value}")


def is_rfc1918_private(ip_str):
    """Kiểm tra IP có thuộc dải mạng nội bộ private RFC 1918 hay không."""
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return ip.is_private and not ip.is_loopback
    except ValueError:
        return False


def is_rfc6598_cgnat(ip_str):
    """Kiểm tra IP có thuộc dải CGNAT 100.64.0.0/10 hay không."""
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        cgnat_net = ipaddress.ip_network("100.64.0.0/10")
        return ip in cgnat_net
    except ValueError:
        return False


def query_tailscale_localapi(endpoint="/localapi/v0/status"):
    """Giao tiếp với Tailscale daemon qua Unix Domain Socket."""
    if not os.path.exists(TAILSCALE_SOCK):
        return None

    class UnixSocketHTTPConnection(http.client.HTTPConnection):
        def __init__(self, sock_path):
            super().__init__("localhost")
            self.sock_path = sock_path

        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect(self.sock_path)

    try:
        conn = UnixSocketHTTPConnection(TAILSCALE_SOCK)
        conn.request("GET", endpoint, headers={"Host": "local-tailscaled.sock"})
        resp = conn.getresponse()
        if resp.status == 200:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except Exception:
        pass
    return None


def get_default_gateway_and_routes():
    """Lấy thông tin Default Gateway từ route của hệ thống."""
    gateway_ip = None
    gateway_dev = None
    try:
        out = subprocess.run(
            ["ip", "-j", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            routes = json.loads(out.stdout)
            if routes and isinstance(routes, list):
                first = routes[0]
                gateway_ip = first.get("gateway")
                gateway_dev = first.get("dev")
    except Exception:
        pass

    # Fallback đọc từ /proc/net/route nếu ip route lỗi
    if not gateway_ip and os.path.exists("/proc/net/route"):
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        gateway_dev = parts[0]
                        hex_gw = parts[2]
                        # Chuyển hex little-endian sang IPv4
                        octets = [str(int(hex_gw[i : i + 2], 16)) for i in (6, 4, 2, 0)]
                        gateway_ip = ".".join(octets)
                        break
        except Exception:
            pass

    return gateway_ip, gateway_dev


def get_dns_servers():
    """Đọc danh sách DNS nameservers từ /etc/resolv.conf."""
    servers = []
    if os.path.exists("/etc/resolv.conf"):
        try:
            with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("nameserver") and len(line.split()) >= 2:
                        servers.append(line.split()[1])
        except Exception:
            pass
    return servers


def _classify_interface_type(name: str) -> str:
    """Phân loại kiểu giao diện mạng dựa trên tên."""
    if name.startswith(("eth", "enp", "eno", "ens", "enx", "em")):
        return "physical"
    if name.startswith(("wlan", "wlp", "wlx", "wifi")):
        return "wifi"
    if name.startswith(("docker", "br-")):
        return "docker"
    if name.startswith(("tailscale", "ts")):
        return "vpn"
    if name.startswith(("tun", "tap", "wg")):
        return "vpn"
    if name.startswith("flannel"):
        return "k8s"
    if name.startswith(("veth", "virbr")):
        return "virtual"
    return "other"


MAC_OUI_MAP = {
    "04:5F:A6": "ZTE / ISP Gateway",
    "10:51:07": "Apple Inc.",
    "E4:0D:36": "Apple Inc.",
    "00:17:F2": "Apple Inc.", "00:1E:52": "Apple Inc.", "00:25:4B": "Apple Inc.",
    "00:26:BB": "Apple Inc.", "04:0C:CE": "Apple Inc.", "14:7D:DA": "Apple Inc.",
    "18:AF:61": "Apple Inc.", "28:CF:DA": "Apple Inc.", "34:36:3B": "Apple Inc.",
    "38:C9:86": "Apple Inc.", "40:6C:8F": "Apple Inc.", "48:D7:05": "Apple Inc.",
    "50:BC:96": "Apple Inc.", "54:26:96": "Apple Inc.", "5C:E9:1E": "Apple Inc.",
    "68:5B:35": "Apple Inc.", "70:56:81": "Apple Inc.", "74:E1:B6": "Apple Inc.",
    "78:4F:43": "Apple Inc.", "7C:D1:C3": "Apple Inc.", "80:49:71": "Apple Inc.",
    "84:38:35": "Apple Inc.", "88:66:5A": "Apple Inc.", "8C:85:90": "Apple Inc.",
    "90:B9:31": "Apple Inc.", "94:94:26": "Apple Inc.", "98:01:A7": "Apple Inc.",
    "9C:20:7B": "Apple Inc.", "A4:83:E7": "Apple Inc.", "AC:BC:32": "Apple Inc.",
    "B0:34:95": "Apple Inc.", "B4:18:D1": "Apple Inc.", "B8:09:8A": "Apple Inc.",
    "BC:52:B7": "Apple Inc.", "C0:84:7A": "Apple Inc.", "C8:6F:1D": "Apple Inc.",
    "DC:A9:04": "Apple Inc.", "F4:37:B7": "Apple Inc.", "F8:27:93": "Apple Inc.",
    "14:49:E0": "Samsung", "18:22:7E": "Samsung", "24:4B:03": "Samsung",
    "2C:AE:2B": "Samsung", "40:16:3B": "Samsung", "50:CC:F8": "Samsung",
    "70:2C:1F": "Samsung", "84:25:DB": "Samsung", "8C:77:12": "Samsung",
    "00:02:B3": "Intel Corp", "00:13:02": "Intel Corp", "00:15:00": "Intel Corp",
    "24:77:03": "Intel Corp", "34:13:E8": "Intel Corp", "48:51:B7": "Intel Corp",
    "18:FE:34": "Espressif IoT", "24:0A:C4": "Espressif IoT", "24:62:AB": "Espressif IoT",
    "30:AE:A4": "Espressif IoT", "84:0D:8E": "Espressif IoT", "A4:CF:12": "Espressif IoT",
    "00:0A:EB": "TP-Link", "14:CC:20": "TP-Link", "50:C7:BF": "TP-Link", "70:4F:57": "TP-Link",
    "00:9E:C8": "Xiaomi", "04:CF:8C": "Xiaomi", "14:F6:5A": "Xiaomi", "74:51:BA": "Xiaomi",
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "52:54:00": "QEMU/KVM Virtual", "00:0C:29": "VMware", "00:50:56": "VMware",
}


def identify_mac_vendor(mac: str) -> str:
    """Xác định nhà sản xuất phần cứng từ tiền tố MAC OUI hoặc Private Wi-Fi MAC."""
    if not mac or mac == "00:00:00:00:00:00":
        return "Unknown"
    try:
        first_byte = int(mac.split(":")[0], 16)
        if first_byte & 2:
            return "Private Wi-Fi (Apple/Android)"
    except Exception:
        pass
    prefix = mac[:8].upper()
    return MAC_OUI_MAP.get(prefix, "Standard LAN Device")


_dns_cache = {}


def resolve_device_name(ip: str, gateway_ip: str = None, vendor: str = None) -> str:
    """Phân giải tên hostname thân thiện cho thiết bị."""
    if not ip:
        return "unknown"
    if gateway_ip and ip == gateway_ip:
        return "Default Gateway (Router)"
    if ip in _dns_cache:
        return _dns_cache[ip]

    hostname = ""
    try:
        hostname = socket.getnameinfo((ip, 0), socket.NI_NAMEREQD)[0]
    except Exception:
        pass

    if hostname:
        _dns_cache[ip] = hostname
        return hostname

    # Tên thân thiện nếu chưa phân giải được DNS
    last_octet = ip.split(".")[-1] if "." in ip else ip.replace(":", "")[-4:]
    if vendor and "Apple" in vendor:
        label = f"Apple-Device-{last_octet}"
    elif vendor and "Private" in vendor:
        label = f"Mobile-{last_octet}"
    elif vendor and "IoT" in vendor:
        label = f"IoT-{last_octet}"
    else:
        clean_ip = ip.replace(":", "-").replace(".", "-")
        label = f"lan-{clean_ip}"

    _dns_cache[ip] = label
    return label


def get_network_interfaces():
    """Lấy danh sách interfaces, IP, MTU, Speed, Duplex, MAC từ `ip -j addr` và /sys."""
    interfaces = []
    try:
        out = subprocess.run(
            ["ip", "-j", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            for item in data:
                ifname = item.get("ifname", "")
                if not ifname or ifname == "lo":
                    continue
                operstate = item.get("operstate", "UNKNOWN")
                mtu = item.get("mtu", 1500)
                ip_list = []
                for a in item.get("addr_info", []):
                    ip_list.append(f"{a.get('local')}/{a.get('prefixlen')}")

                # Đọc thông số phần cứng từ /sys/class/net/<ifname>
                speed_str = "N/A"
                duplex_str = "N/A"
                mac_str = "N/A"
                sys_base = f"/sys/class/net/{ifname}"
                if os.path.exists(sys_base):
                    try:
                        with open(f"{sys_base}/address", "r", encoding="utf-8") as f:
                            m = f.read().strip()
                            if m and m != "00:00:00:00:00:00":
                                mac_str = m
                    except Exception:
                        pass
                    try:
                        with open(f"{sys_base}/speed", "r", encoding="utf-8") as f:
                            s = f.read().strip()
                            if s and s != "-1" and int(s) > 0:
                                speed_str = f"{s} Mbps"
                    except Exception:
                        pass
                    try:
                        with open(f"{sys_base}/duplex", "r", encoding="utf-8") as f:
                            d = f.read().strip()
                            if d and d != "unknown":
                                duplex_str = d
                    except Exception:
                        pass

                interfaces.append({
                    "name": ifname,
                    "state": operstate,
                    "mtu": mtu,
                    "ips": ip_list,
                    "net_type": _classify_interface_type(ifname),
                    "speed": speed_str,
                    "duplex": duplex_str,
                    "mac": mac_str,
                })
    except Exception:
        pass
    return interfaces


def get_arp_neighbors():
    """Lấy danh sách thiết bị trong mạng LAN từ bảng ARP (/proc/net/arp hoặc ip neigh)."""
    devices = []
    try:
        out = subprocess.run(
            ["ip", "-j", "neigh"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            for n in data:
                ip = n.get("dst")
                mac = n.get("lladdr")
                dev = n.get("dev", "")
                state = " ".join(n.get("state", []))
                if ip and mac and mac != "00:00:00:00:00:00":
                    is_reachable = any(s in state for s in ["REACHABLE", "PERMANENT", "DELAY", "PROBE"])
                    devices.append({
                        "ip": ip,
                        "mac": mac,
                        "dev": dev,
                        "reachable": is_reachable,
                    })
            return devices
    except Exception:
        pass

    # Fallback /proc/net/arp
    if os.path.exists("/proc/net/arp"):
        try:
            with open("/proc/net/arp", "r", encoding="utf-8") as f:
                lines = f.readlines()[1:]
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 6:
                        ip, _, _, mac, _, dev = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                        if mac != "00:00:00:00:00:00":
                            devices.append({
                                "ip": ip,
                                "mac": mac,
                                "dev": dev,
                                "reachable": True,
                            })
        except Exception:
            pass
    return devices


def detect_stun_nat_mapping():
    """
    Kiểm tra kiểu NAT (Cone vs Symmetric NAT) bằng STUN RFC 5389 thuần Python.
    Gửi Binding Request từ cùng một local UDP socket đến 2 máy chủ STUN của Google.
    Nếu cổng ngoại mạng (mapped port) bằng nhau => Cone NAT. Nếu khác nhau => Symmetric NAT.
    """
    stun_servers = [("stun.l.google.com", 19302), ("stun1.l.google.com", 19302)]
    mapped_ports = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        # Binding request header (20 bytes): Type 0x0001, Length 0, Magic Cookie 0x2112A442, Transaction ID (12 bytes)
        req = b"\x00\x01\x00\x00\x21\x12\xa4\x42" + os.urandom(12)
        for host, port in stun_servers:
            try:
                sock.sendto(req, (host, port))
                data, _ = sock.recvfrom(512)
                # Parse XOR-MAPPED-ADDRESS attribute (0x0020)
                if len(data) > 20:
                    pos = 20
                    while pos + 4 <= len(data):
                        attr_type = int.from_bytes(data[pos : pos + 2], "big")
                        attr_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
                        pos += 4
                        if attr_type == 0x0020 and attr_len >= 8:
                            # XOR port = port ^ 0x2112
                            raw_port = int.from_bytes(data[pos + 2 : pos + 4], "big")
                            x_port = raw_port ^ 0x2112
                            mapped_ports.append(x_port)
                            break
                        pos += attr_len
            except Exception:
                pass
    finally:
        sock.close()

    if len(mapped_ports) >= 2:
        # Nếu mapped port thay đổi khi đích đổi => Symmetric NAT
        is_symmetric = 1 if mapped_ports[0] != mapped_ports[1] else 0
        return is_symmetric
    return 0


def detect_double_nat_and_cgnat(gateway_ip):
    """
    Dùng traceroute ngắn (max 3 hops) để phát hiện Double NAT và CGNAT.
    - Double NAT: Cả hop 1 và hop 2 đều là private RFC 1918.
    - CGNAT: Hop 2 hoặc WAN nằm trong 100.64.0.0/10.
    """
    double_nat = 0
    cgnat = 0
    try:
        out = subprocess.run(
            ["traceroute", "-n", "-m", "3", "-w", "1", "-q", "1", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if out.returncode == 0:
            lines = out.stdout.strip().splitlines()
            hops = []
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].isdigit():
                    hop_ip = parts[1]
                    if hop_ip != "*":
                        hops.append(hop_ip)

            if len(hops) >= 2:
                hop1, hop2 = hops[0], hops[1]
                # Nếu hop 1 và hop 2 đều là Private IP nội bộ -> Double NAT
                if is_rfc1918_private(hop1) and is_rfc1918_private(hop2):
                    double_nat = 1
                # Nếu hop 2 thuộc dải 100.64.0.0/10 -> CGNAT
                if is_rfc6598_cgnat(hop2):
                    cgnat = 1
    except Exception:
        pass

    return double_nat, cgnat


def ping_target(target):
    """Đo độ trễ (latency ms) đến một IP mục tiêu bằng ping 1 gói."""
    if not target:
        return None
    try:
        out = subprocess.run(
            ["ping", "-c", "1", "-W", "1", str(target)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            match = re.search(r"time=([0-9.]+)\s*ms", out.stdout)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    return None


def get_public_ip_and_isp():
    """Lấy thông tin IP Public (cả IPv4 và IPv6 riêng biệt) và ISP."""
    public_ipv4 = "None"
    public_ipv6 = "None"
    isp = "unknown"

    # 1. Thử lấy IPv4 thật (chỉ chấp nhận địa chỉ version 4)
    ipv4_endpoints = [
        ["curl", "-4", "-s", "--max-time", "3", "https://api.ipify.org?format=json"],
        ["curl", "-4", "-s", "--max-time", "3", "https://ifconfig.co/json"],
        ["curl", "-4", "-s", "--max-time", "2", "https://ipv4.icanhazip.com"],
        ["curl", "-4", "-s", "--max-time", "2", "https://v4.ident.me"],
    ]
    for cmd in ipv4_endpoints:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            if out.returncode == 0 and out.stdout.strip():
                raw = out.stdout.strip()
                cand = ""
                if raw.startswith("{"):
                    try:
                        d = json.loads(raw)
                        cand = d.get("ip", "")
                        if isp == "unknown":
                            isp = d.get("asn_org", d.get("isp", "unknown"))
                    except Exception:
                        pass
                else:
                    cand = raw
                if cand:
                    try:
                        addr = ipaddress.ip_address(cand.strip())
                        if addr.version == 4:
                            public_ipv4 = str(addr)
                            break
                    except ValueError:
                        pass
        except Exception:
            pass

    # 2. Thử lấy IPv6 thật (chỉ chấp nhận địa chỉ version 6 public toàn cầu)
    ipv6_endpoints = [
        ["curl", "-6", "-s", "--max-time", "3", "https://api6.ipify.org?format=json"],
        ["curl", "-6", "-s", "--max-time", "3", "https://ifconfig.co/json"],
        ["curl", "-6", "-s", "--max-time", "2", "https://ipv6.icanhazip.com"],
        ["curl", "-6", "-s", "--max-time", "2", "https://v6.ident.me"],
    ]
    for cmd in ipv6_endpoints:
        try:
            out6 = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            if out6.returncode == 0 and out6.stdout.strip():
                raw6 = out6.stdout.strip()
                cand6 = ""
                if raw6.startswith("{"):
                    try:
                        d6 = json.loads(raw6)
                        cand6 = d6.get("ip", "")
                        if isp == "unknown":
                            isp = d6.get("asn_org", d6.get("isp", "unknown"))
                    except Exception:
                        pass
                else:
                    cand6 = raw6
                if cand6:
                    try:
                        addr6 = ipaddress.ip_address(cand6.strip())
                        if addr6.version == 6 and not addr6.is_private and not addr6.is_link_local and not addr6.is_loopback:
                            public_ipv6 = str(addr6)
                            break
                    except ValueError:
                        pass
        except Exception:
            pass

    # 3. Fallback đọc IPv6 global unicast (2000::/3) từ card mạng nếu curl -6 timeout
    if public_ipv6 == "None":
        try:
            out_ip = subprocess.run(
                ["ip", "-j", "-6", "addr", "show", "scope", "global"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out_ip.returncode == 0 and out_ip.stdout.strip():
                data = json.loads(out_ip.stdout)
                for iface in data:
                    for a in iface.get("addr_info", []):
                        cand = a.get("local", "")
                        try:
                            addr = ipaddress.ip_address(cand)
                            if addr.version == 6 and not addr.is_private and not addr.is_link_local:
                                public_ipv6 = str(addr)
                                break
                        except ValueError:
                            pass
                    if public_ipv6 != "None":
                        break
        except Exception:
            pass

    return public_ipv4, public_ipv6, isp


def get_open_listening_ports():
    """Lấy danh sách các cổng đang mở lắng nghe bằng `ss -tulpn`."""
    ports = []
    try:
        out = subprocess.run(
            ["ss", "-H", "-tulpn"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    proto = parts[0].lower()
                    local_addr = parts[4]
                    proc_info = parts[6] if len(parts) >= 7 else "-"

                    # Tách IP và Port
                    if ":" in local_addr:
                        r_idx = local_addr.rfind(":")
                        ip_part = local_addr[:r_idx].strip("[]* ")
                        port_part = local_addr[r_idx + 1 :]

                        # Phân loại mức độ phơi nhiễm (Exposure)
                        if not ip_part or ip_part in ["0.0.0.0", "::"]:
                            exposure = "Public / LAN"
                        elif ip_part.startswith("100."):
                            exposure = "Tailscale Only"
                        elif ip_part in ["127.0.0.1", "::1"]:
                            exposure = "Localhost Only"
                        else:
                            exposure = "Specific LAN"

                        # Trích xuất process name
                        proc_match = re.search(r'"([^"]+)"', proc_info)
                        service_name = proc_match.group(1) if proc_match else proc_info

                        ports.append({
                            "proto": proto,
                            "port": port_part,
                            "bind_ip": ip_part or "0.0.0.0",
                            "exposure": exposure,
                            "service": service_name,
                        })
    except Exception:
        pass
    return ports


def get_active_connections():
    """Lấy danh sách các kết nối đang thông suốt (ESTABLISHED) bằng `ss -tan`."""
    conns = []
    state_counts = {}
    try:
        out = subprocess.run(
            ["ss", "-H", "-tan"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    state = parts[0].upper()
                    state_counts[state] = state_counts.get(state, 0) + 1

                    if state == "ESTAB":
                        local = parts[3]
                        remote = parts[4]
                        l_port = local.rsplit(":", 1)[-1] if ":" in local else local
                        if ":" in remote:
                            r_ip, r_port = remote.rsplit(":", 1)
                            r_ip = r_ip.strip("[]")
                            conns.append({
                                "proto": "tcp",
                                "local_port": l_port,
                                "remote_ip": r_ip,
                                "remote_port": r_port,
                                "state": "ESTABLISHED",
                            })
    except Exception:
        pass
    return state_counts, conns[:25]  # Giới hạn 25 kết nối tiêu biểu để tránh quá tải


def get_firewall_rules_and_policies():
    """Lấy danh sách các quy tắc iptables chính và default policy."""
    policies = {"INPUT": "ACCEPT", "FORWARD": "ACCEPT", "OUTPUT": "ACCEPT"}
    rules = []
    try:
        out = subprocess.run(
            ["iptables", "-L", "-v", "-n"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            current_chain = ""
            for line in out.stdout.splitlines():
                line = line.strip()
                if line.startswith("Chain "):
                    # Chain INPUT (policy DROP 12 packets, 450 bytes)
                    m = re.match(r"Chain\s+([A-Z0-9_-]+)\s+\(policy\s+([A-Z]+)", line)
                    if m:
                        current_chain = m.group(1)
                        if current_chain in policies:
                            policies[current_chain] = m.group(2)
                    else:
                        current_chain = line.split()[1]
                    continue

                # Bỏ qua các chain nội bộ Docker để bảng rule sạch sẽ
                if current_chain.startswith("DOCKER") or current_chain in ["br-", "veth"]:
                    continue

                parts = line.split()
                if len(parts) >= 9 and parts[0].isdigit():
                    pkts = parts[0]
                    bytes_cnt = parts[1]
                    target = parts[2]
                    proto = parts[3]
                    in_if = parts[5]
                    out_if = parts[6]
                    source = parts[7]
                    dest = parts[8]
                    extra = " ".join(parts[9:]) if len(parts) > 9 else "-"

                    rules.append({
                        "chain": current_chain,
                        "target": target,
                        "proto": proto,
                        "in": in_if,
                        "out": out_if,
                        "source": source,
                        "dest": dest,
                        "pkts": pkts,
                        "bytes": bytes_cnt,
                        "extra": extra,
                    })
    except Exception:
        pass
    return policies, rules[:30]


def get_kernel_security_settings():
    """Đọc các thiết lập an ninh mạng sysctl từ /proc/sys/net/ipv4."""
    base = "/proc/sys/net/ipv4"
    if not os.path.exists(base) and os.path.exists("/host/proc/sys/net/ipv4"):
        base = "/host/proc/sys/net/ipv4"

    settings = {
        "ip_forward": 0,
        "tcp_syncookies": 1,
        "accept_redirects": 0,
        "rp_filter": 1,
    }

    def read_val(rel_path):
        p = os.path.join(base, rel_path)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return int(f.read().strip())
            except Exception:
                pass
        return None

    v = read_val("ip_forward")
    if v is not None:
        settings["ip_forward"] = v

    v = read_val("tcp_syncookies")
    if v is not None:
        settings["tcp_syncookies"] = v

    v = read_val("conf/all/accept_redirects")
    if v is not None:
        settings["accept_redirects"] = v

    v = read_val("conf/all/rp_filter")
    if v is not None:
        settings["rp_filter"] = v

    return settings


def collect_network_metrics(lines):
    """Hàm thu thập tổng hợp toàn bộ thông số mạng và bảo mật."""
    now = time.time()
    gateway_ip, gateway_dev = get_default_gateway_and_routes()
    dns_servers = get_dns_servers()

    # 1. Cập nhật các kiểm tra chậm (chạy mỗi SLOW_INTERVAL = 120s)
    if now - slow_cache["last_run"] >= SLOW_INTERVAL:
        slow_cache["last_run"] = now

        # Kiểm tra Tailscale status & netcheck
        ts_status = query_tailscale_localapi("/localapi/v0/status")
        if ts_status:
            slow_cache["hairpinning"] = 1 if ts_status.get("HairPinning") else 0
            if "MappingVariesByDestIP" in ts_status:
                slow_cache["symmetric"] = 1 if ts_status.get("MappingVariesByDestIP") else 0
            else:
                slow_cache["symmetric"] = detect_stun_nat_mapping()
        else:
            slow_cache["symmetric"] = detect_stun_nat_mapping()

        # Kiểm tra Double NAT & CGNAT qua traceroute
        d_nat, cgnat_hop = detect_double_nat_and_cgnat(gateway_ip)
        slow_cache["double_nat"] = d_nat

        # Lấy public IP (cả IPv4 và IPv6)
        pub_ip, pub_ipv6, isp_name = get_public_ip_and_isp()
        slow_cache["public_ipv4"] = pub_ip
        slow_cache["public_ipv6"] = pub_ipv6
        slow_cache["isp"] = isp_name

        # Đánh giá CGNAT: nếu WAN IP hoặc hop 2 nằm trong 100.64.0.0/10
        if is_rfc6598_cgnat(pub_ip) or cgnat_hop == 1:
            slow_cache["cgnat"] = 1
        else:
            slow_cache["cgnat"] = 0

        # Đo ping latency
        if gateway_ip:
            slow_cache["ping_gateway"] = ping_target(gateway_ip)
        if dns_servers:
            slow_cache["ping_dns"] = ping_target(dns_servers[0])
        slow_cache["ping_internet"] = ping_target("1.1.1.1")

    # 2. Xuất metric NAT & WAN
    add_sample(lines, "net_nat_is_cgnat", slow_cache["cgnat"])
    add_sample(lines, "net_nat_is_symmetric", slow_cache["symmetric"])
    add_sample(lines, "net_nat_is_double_nat", slow_cache["double_nat"])
    add_sample(lines, "net_nat_hairpinning", slow_cache["hairpinning"])
    add_sample(
        lines,
        "net_wan_info",
        1,
        {
            "public_ipv4": slow_cache["public_ipv4"],
            "public_ipv6": slow_cache["public_ipv6"],
            "isp": slow_cache["isp"],
        },
    )

    # 3. Xuất Gateway & DNS
    gw_mac = "unknown"
    arp_entries = get_arp_neighbors()
    if gateway_ip:
        for a in arp_entries:
            if a["ip"] == gateway_ip:
                gw_mac = a["mac"]
                break
        add_sample(
            lines,
            "net_gateway_info",
            1,
            {"gateway_ip": gateway_ip, "interface": gateway_dev or "unknown", "gateway_mac": gw_mac},
        )

    for idx, dns in enumerate(dns_servers[:3]):
        add_sample(lines, "net_dns_server_info", 1, {"dns_ip": dns, "priority": str(idx + 1)})

    # 4. Xuất Latency
    if slow_cache["ping_gateway"] is not None:
        add_sample(lines, "net_ping_latency_ms", slow_cache["ping_gateway"], {"target": "gateway"})
    if slow_cache["ping_dns"] is not None:
        add_sample(lines, "net_ping_latency_ms", slow_cache["ping_dns"], {"target": "dns"})
    if slow_cache["ping_internet"] is not None:
        add_sample(lines, "net_ping_latency_ms", slow_cache["ping_internet"], {"target": "internet"})

    # 5. Xuất Topology Interfaces
    for iface in get_network_interfaces():
        ip_summary = ", ".join(iface["ips"]) if iface["ips"] else "no-ip"
        add_sample(
            lines,
            "net_interface_info",
            1,
            {
                "name": iface["name"],
                "state": iface["state"],
                "mtu": str(iface["mtu"]),
                "ips": ip_summary,
                "net_type": iface.get("net_type", "other"),
                "speed": iface.get("speed", "N/A"),
                "duplex": iface.get("duplex", "N/A"),
                "mac": iface.get("mac", "N/A"),
            },
        )

    # 6. Kho Thiết bị Hợp nhất (Unified Device Inventory: LAN + Tailscale)
    # Thiết bị LAN
    for a in arp_entries:
        status = "reachable" if a["reachable"] else "stale"
        vendor = identify_mac_vendor(a["mac"])
        dev_name = resolve_device_name(a["ip"], gateway_ip=gateway_ip, vendor=vendor)
        add_sample(
            lines,
            "net_device_info",
            1,
            {
                "network": "LAN",
                "name": dev_name,
                "ip": a["ip"],
                "identifier": a["mac"],
                "vendor": vendor,
                "status": status,
                "link_type": "direct",
            },
        )

    # Thiết bị Tailscale
    ts_status = query_tailscale_localapi("/localapi/v0/status")
    if ts_status:
        ts_self = ts_status.get("Self", {})
        add_sample(
            lines,
            "net_tailscale_status",
            1 if ts_self.get("Online") else 0,
            {
                "hostname": ts_self.get("HostName", "unknown"),
                "tailscale_ip": ts_self.get("TailscaleIPs", [""])[0],
                "os": ts_self.get("OS", "linux"),
            },
        )

        peers = ts_status.get("Peer", {})
        for _, p in peers.items():
            name = p.get("HostName", "unknown")
            ip = p.get("TailscaleIPs", [""])[0]
            status = "online" if p.get("Online") else "offline"
            link = "direct" if p.get("CurAddr") else f"relay-{p.get('Relay', 'derp')}"
            os_name = p.get("OS", "node").capitalize()
            add_sample(
                lines,
                "net_device_info",
                1,
                {
                    "network": "Tailscale",
                    "name": name,
                    "ip": ip,
                    "identifier": p.get("OS", "node"),
                    "vendor": f"Tailscale ({os_name})",
                    "status": status,
                    "link_type": link,
                },
            )

    # 7. Cổng mở lắng nghe & Kết nối
    open_ports = get_open_listening_ports()
    for p in open_ports:
        add_sample(
            lines,
            "net_listening_port_info",
            1,
            {
                "proto": p["proto"],
                "port": str(p["port"]),
                "bind_ip": p["bind_ip"],
                "exposure": p["exposure"],
                "service": p["service"],
            },
        )

    state_counts, active_conns = get_active_connections()
    for st, count in state_counts.items():
        add_sample(lines, "net_active_connections_total", count, {"state": st})

    for c in active_conns:
        add_sample(
            lines,
            "net_active_connection_info",
            1,
            {
                "proto": c["proto"],
                "local_port": str(c["local_port"]),
                "remote_ip": c["remote_ip"],
                "remote_port": str(c["remote_port"]),
                "state": c["state"],
            },
        )

    # 8. Firewall & iptables rules
    policies, rules = get_firewall_rules_and_policies()
    for chain, pol in policies.items():
        add_sample(lines, "net_firewall_default_policy", 1, {"chain": chain, "policy": pol})

    for r in rules:
        add_sample(
            lines,
            "net_firewall_rule_info",
            1,
            {
                "chain": r["chain"],
                "target": r["target"],
                "proto": r["proto"],
                "source": r["source"],
                "dest": r["dest"],
                "packets": str(r["pkts"]),
                "bytes": str(r["bytes"]),
                "extra": r["extra"][:40],
            },
        )

    # 9. An ninh Kernel sysctl
    sec = get_kernel_security_settings()
    add_sample(lines, "net_security_setting", sec["ip_forward"], {"setting": "ip_forward"})
    add_sample(lines, "net_security_setting", sec["tcp_syncookies"], {"setting": "tcp_syncookies"})
    add_sample(lines, "net_security_setting", sec["accept_redirects"], {"setting": "accept_redirects"})
    add_sample(lines, "net_security_setting", sec["rp_filter"], {"setting": "rp_filter"})


def write_atomic(lines, output_path=None):
    """Ghi đè file metric nguyên tử để tránh Prometheus đọc dở dang."""
    target_path = output_path or OUTPUT
    directory = os.path.dirname(target_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".net_metrics.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target_path)
    finally:
        if os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def collect_once(output_path=None):
    """Thực hiện một chu kỳ thu thập."""
    lines = [
        "# HELP net_collector_success Trang thai thu thap metric mang gan nhat.",
        "# TYPE net_collector_success gauge",
        "# HELP net_collector_timestamp_seconds Thoi diem thu thap metric mang Unix.",
        "# TYPE net_collector_timestamp_seconds gauge",
        "# HELP net_nat_is_cgnat May co dang o sau mang CGNAT khong (1=co, 0=khong).",
        "# TYPE net_nat_is_cgnat gauge",
        "# HELP net_nat_is_symmetric Kieu NAT co phai Symmetric NAT khong (1=co, 0=Cone).",
        "# TYPE net_nat_is_symmetric gauge",
        "# HELP net_nat_is_double_nat Co phat hien Double NAT da tang router khong (1=co, 0=khong).",
        "# TYPE net_nat_is_double_nat gauge",
        "# HELP net_nat_hairpinning Router co ho tro NAT Loopback khong (1=co, 0=khong).",
        "# TYPE net_nat_hairpinning gauge",
        "# HELP net_wan_info Thong tin dia chi IP Public va nha mang ISP ngoai.",
        "# TYPE net_wan_info gauge",
        "# HELP net_gateway_info Thong tin Default Gateway IP va MAC.",
        "# TYPE net_gateway_info gauge",
        "# HELP net_dns_server_info Thong tin DNS nameservers.",
        "# TYPE net_dns_server_info gauge",
        "# HELP net_ping_latency_ms Do tre ping den cac dich muc tieu.",
        "# TYPE net_ping_latency_ms gauge",
        "# HELP net_interface_info Danh sach cac card mang host.",
        "# TYPE net_interface_info gauge",
        "# HELP net_device_info Danh sach thiet bi hop nhat trong LAN va Tailscale.",
        "# TYPE net_device_info gauge",
        "# HELP net_tailscale_status Trang thai ket noi Tailscale cua may.",
        "# TYPE net_tailscale_status gauge",
        "# HELP net_listening_port_info Cac cong mo dang lang nghe tren may.",
        "# TYPE net_listening_port_info gauge",
        "# HELP net_active_connections_total Tong so ket noi theo trang thai.",
        "# TYPE net_active_connections_total gauge",
        "# HELP net_active_connection_info Danh sach cac ket noi dang thong suot.",
        "# TYPE net_active_connection_info gauge",
        "# HELP net_firewall_default_policy Chinh sach mac dinh cua cac chain iptables.",
        "# TYPE net_firewall_default_policy gauge",
        "# HELP net_firewall_rule_info Quy tac tuong lua iptables.",
        "# TYPE net_firewall_rule_info gauge",
        "# HELP net_security_setting Thiet lap bao mat mang kernel sysctl.",
        "# TYPE net_security_setting gauge",
    ]
    add_sample(lines, "net_collector_success", 1)
    add_sample(lines, "net_collector_timestamp_seconds", time.time())
    try:
        collect_network_metrics(lines)
    except Exception as e:
        print(f"Lỗi thu thập mạng: {e}", file=sys.stderr)
        add_sample(lines, "net_collector_success", 0)

    write_atomic(lines, output_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Network & Security Metrics Collector")
    parser.add_argument("--loop", action="store_true", help="Chạy vòng lặp định kỳ liên tục")
    parser.add_argument("--oneshot", action="store_true", help="Chạy một lần duy nhất rồi thoát")
    parser.add_argument("--output", default=OUTPUT, help="Đường dẫn file .prom đầu ra")
    parser.add_argument(
        "--interval",
        type=float,
        default=FAST_INTERVAL,
        help="Chu kỳ thu thập tính bằng giây",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    should_loop = args.loop or (not args.oneshot and os.environ.get("COLLECTOR_LOOP", "1") == "1")

    if not should_loop:
        return collect_once(args.output)

    running = True

    def handle_signal(sig, _):
        nonlocal running
        print(f"Nhận tín hiệu {sig}, đang dừng collector...", file=sys.stderr)
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print(f"Bắt đầu network collector vòng lặp mỗi {args.interval}s, xuất {args.output}")
    while running:
        collect_once(args.output)
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())


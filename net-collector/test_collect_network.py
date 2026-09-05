import io
import os
import tempfile
import unittest
from unittest import mock

import collect_network


class NetworkCollectorTests(unittest.TestCase):
    def test_rfc1918_private_ips(self):
        self.assertTrue(collect_network.is_rfc1918_private("192.168.1.1"))
        self.assertTrue(collect_network.is_rfc1918_private("10.0.0.1"))
        self.assertTrue(collect_network.is_rfc1918_private("172.16.5.10"))
        self.assertFalse(collect_network.is_rfc1918_private("8.8.8.8"))
        self.assertFalse(collect_network.is_rfc1918_private("1.1.1.1"))
        self.assertFalse(collect_network.is_rfc1918_private("100.64.0.1"))
        self.assertFalse(collect_network.is_rfc1918_private("127.0.0.1"))

    def test_rfc6598_cgnat_ips(self):
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.64.0.1"))
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.100.50.1"))
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.127.255.254"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("192.168.1.1"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("8.8.8.8"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("invalid-ip"))

    def test_prometheus_escape(self):
        self.assertEqual(collect_network.prometheus_escape('hello "world"'), 'hello \\"world\\"')
        self.assertEqual(collect_network.prometheus_escape("line1\nline2"), "line1 line2")
        self.assertEqual(collect_network.prometheus_escape("back\\slash"), "back\\\\slash")

    def test_add_sample_with_and_without_labels(self):
        lines = []
        collect_network.add_sample(lines, "test_metric", 42)
        self.assertIn("test_metric 42", lines)

        collect_network.add_sample(lines, "test_metric_labels", 1, {"foo": "bar", "num": "10"})
        self.assertIn('test_metric_labels{foo="bar",num="10"} 1', lines)

    def test_tailscale_peer_parsing(self):
        mock_status = {
            "Self": {
                "HostName": "node-host",
                "TailscaleIPs": ["100.64.1.1"],
                "Online": True,
                "OS": "linux",
            },
            "Peer": {
                "node1": {
                    "HostName": "laptop",
                    "TailscaleIPs": ["100.64.1.2"],
                    "Online": True,
                    "CurAddr": "192.168.1.15:41641",
                    "OS": "macos",
                },
                "node2": {
                    "HostName": "phone",
                    "TailscaleIPs": ["100.64.1.3"],
                    "Online": False,
                    "CurAddr": "",
                    "Relay": "sgp",
                    "OS": "ios",
                },
            },
        }

        lines = []
        with mock.patch.object(collect_network, "query_tailscale_localapi", return_value=mock_status):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any("net_tailscale_status" in l and 'hostname="node-host"' in l for l in lines))
        self.assertTrue(any('network="Tailscale"' in l and 'name="laptop"' in l and 'link_type="direct"' in l for l in lines))
        self.assertTrue(any('network="Tailscale"' in l and 'name="phone"' in l and 'status="offline"' in l for l in lines))

    def test_arp_neighbors_parsing(self):
        mock_arp = [
            {"ip": "192.168.1.1", "mac": "00:11:22:33:44:55", "dev": "enp3s0", "reachable": True},
            {"ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff", "dev": "enp3s0", "reachable": False},
        ]
        lines = []
        with (
            mock.patch.object(collect_network, "get_arp_neighbors", return_value=mock_arp),
            mock.patch.object(collect_network, "get_default_gateway_and_routes", return_value=("192.168.1.1", "enp3s0")),
        ):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any('network="LAN"' in l and 'ip="192.168.1.1"' in l and 'identifier="00:11:22:33:44:55"' in l and 'status="reachable"' in l for l in lines))
        self.assertTrue(any('network="LAN"' in l and 'ip="192.168.1.100"' in l and 'status="stale"' in l for l in lines))
        self.assertTrue(any("net_gateway_info" in l and 'gateway_ip="192.168.1.1"' in l for l in lines))

    def test_open_ports_and_connections(self):
        mock_ports = [
            {"proto": "tcp", "port": "22", "bind_ip": "0.0.0.0", "exposure": "Public / LAN", "service": "sshd"},
            {"proto": "tcp", "port": "3000", "bind_ip": "100.64.1.1", "exposure": "Tailscale Only", "service": "grafana"},
        ]
        mock_states = {"ESTAB": 5, "TIME_WAIT": 2}
        mock_conns = [
            {"proto": "tcp", "local_port": "3000", "remote_ip": "100.64.1.2", "remote_port": "54321", "state": "ESTABLISHED"}
        ]
        lines = []
        with (
            mock.patch.object(collect_network, "get_open_listening_ports", return_value=mock_ports),
            mock.patch.object(collect_network, "get_active_connections", return_value=(mock_states, mock_conns)),
        ):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any("net_listening_port_info" in l and 'port="22"' in l and 'exposure="Public / LAN"' in l for l in lines))
        self.assertTrue(any("net_listening_port_info" in l and 'port="3000"' in l and 'exposure="Tailscale Only"' in l for l in lines))
        self.assertTrue(any('net_active_connections_total{state="ESTAB"} 5' in l for l in lines))
        self.assertTrue(any("net_active_connection_info" in l and 'local_port="3000"' in l for l in lines))

    def test_collect_once_writes_atomic_file(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.exists(temp_dir) and os.system(f"rm -rf {temp_dir}"))
        target_path = os.path.join(temp_dir, "test_network.prom")

        ret = collect_network.collect_once(output_path=target_path)
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.exists(target_path))

        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("net_collector_success 1", content)
        self.assertIn("net_nat_is_cgnat", content)
    def test_identify_mac_vendor(self):
        self.assertEqual(collect_network.identify_mac_vendor("10:51:07:94:68:7b"), "Apple Inc.")
        self.assertEqual(collect_network.identify_mac_vendor("e4:0d:36:cd:1e:3c"), "Apple Inc.")
        self.assertEqual(collect_network.identify_mac_vendor("04:5f:a6:8f:1c:6d"), "ZTE / ISP Gateway")
        self.assertEqual(collect_network.identify_mac_vendor("06:d1:50:f2:9b:0a"), "Private Wi-Fi (Apple/Android)")
        self.assertEqual(collect_network.identify_mac_vendor("ee:4d:50:9c:2d:79"), "Private Wi-Fi (Apple/Android)")

    def test_resolve_device_name(self):
        self.assertEqual(collect_network.resolve_device_name("192.168.1.1", gateway_ip="192.168.1.1"), "Default Gateway (Router)")
        name = collect_network.resolve_device_name("192.168.1.100", vendor="Apple Inc.")
        self.assertEqual(name, "Apple-Device-100")

    def test_resolve_port_service(self):
        # Khi có tên tiến trình thực tế
        self.assertEqual(collect_network.resolve_port_service("41641", "udp", "tailscaled"), "tailscaled")
        self.assertEqual(collect_network.resolve_port_service("10250", "tcp", "kubelet"), "kubelet")
        
        # Khi tiến trình bị rỗng (-) hoặc rỗng, dùng bảng tra cứu WELL_KNOWN_SERVICES
        self.assertEqual(collect_network.resolve_port_service("53", "tcp", "-"), "systemd-resolved (DNS)")
        self.assertEqual(collect_network.resolve_port_service("53", "udp", ""), "systemd-resolved (DNS)")
        self.assertEqual(collect_network.resolve_port_service("10250", "tcp", "-"), "kubelet (K8s API)")
        self.assertEqual(collect_network.resolve_port_service("41641", "udp", "-"), "tailscale (DERP/WireGuard)")
        self.assertEqual(collect_network.resolve_port_service("3000", "tcp", "-"), "grafana")

    def test_multi_target_latency_emission(self):
        collect_network.slow_cache["latencies"] = {
            "gateway": {"val": 0.8, "service": "Gateway", "category": "local_network"},
            "vn_viettel": {"val": 3.2, "service": "Viettel DNS", "category": "domestic_vn"},
            "cloudflare": {"val": 25.1, "service": "Cloudflare (1.1.1.1)", "category": "global_dns"},
            "youtube": {"val": 12.5, "service": "YouTube", "category": "media_service"},
        }
        lines = []
        collect_network.collect_network_metrics(lines)
        self.assertTrue(any('net_ping_latency_ms{category="local_network",service="Gateway",target="gateway"} 0.8' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="domestic_vn",service="Viettel DNS",target="vn_viettel"} 3.2' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="global_dns",service="Cloudflare (1.1.1.1)",target="cloudflare"} 25.1' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="media_service",service="YouTube",target="youtube"} 12.5' in l for l in lines))

    def test_get_open_listening_ports_ss_parsing(self):
        mock_ss_output = """tcp LISTEN 0 128 0.0.0.0:10250 0.0.0.0:* users:(("kubelet",pid=800,fd=10))
udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=320,fd=12))
udp UNCONN 0 0 0.0.0.0:41641 0.0.0.0:* -
tcp LISTEN 0 128 [::]:3000 [::]:* users:(("grafana",pid=1500,fd=7))
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_ss_output)
            ports = collect_network.get_open_listening_ports()

        self.assertEqual(len(ports), 4)
        
        # 10250 kubelet
        p_10250 = next(p for p in ports if p["port"] == "10250")
        self.assertEqual(p_10250["service"], "kubelet")
        self.assertEqual(p_10250["exposure"], "Public / LAN")

        # 53 systemd-resolved on loopback %lo
        p_53 = next(p for p in ports if p["port"] == "53")
        self.assertEqual(p_53["service"], "systemd-resolve")
        self.assertEqual(p_53["exposure"], "Localhost Only")

        # 41641 tailscale (khi ss trả về `-`, fallback sang WELL_KNOWN_SERVICES)
        p_41641 = next(p for p in ports if p["port"] == "41641")
        self.assertEqual(p_41641["service"], "tailscale (DERP/WireGuard)")
        self.assertEqual(p_41641["exposure"], "Public / LAN")

        # 3000 grafana on ::
        p_3000 = next(p for p in ports if p["port"] == "3000")
        self.assertEqual(p_3000["service"], "grafana")
        self.assertEqual(p_3000["exposure"], "Public / LAN")


if __name__ == "__main__":
    unittest.main()


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
        self.assertIn("net_nat_is_symmetric", content)


if __name__ == "__main__":
    unittest.main()


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests cho Windows Unified Monitoring Agent & Blackbox Crash Engine.
"""

import unittest
from unittest import mock
import os
import sys

# Thêm thư mục windows vào sys.path để import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent


class WindowsAgentTests(unittest.TestCase):

    def test_parse_bugcheck_code(self):
        # 1. Mã 0 (Hard Power Off / Quá nhiệt ngắt nguồn)
        b_int, b_hex, b_name, b_desc = agent.parse_bugcheck_code(0)
        self.assertEqual(b_int, 0)
        self.assertEqual(b_hex, "0x00000000")
        self.assertEqual(b_name, "HARD_POWER_OFF_OR_THERMAL_TRIP")

        # 2. Mã 0x116 (278: VIDEO_TDR_FAILURE - GPU Crash)
        b_int, b_hex, b_name, b_desc = agent.parse_bugcheck_code(278)
        self.assertEqual(b_int, 278)
        self.assertEqual(b_hex, "0x00000116")
        self.assertEqual(b_name, "VIDEO_TDR_FAILURE")

        # 3. Mã 0x124 (292: WHEA Hardware Error)
        b_int, b_hex, b_name, b_desc = agent.parse_bugcheck_code("0x124")
        self.assertEqual(b_int, 292)
        self.assertEqual(b_hex, "0x00000124")
        self.assertEqual(b_name, "WHEA_UNCORRECTABLE_ERROR")

        # 4. Mã lạ không có trong từ điển
        b_int, b_hex, b_name, b_desc = agent.parse_bugcheck_code(9999)
        self.assertEqual(b_int, 9999)
        self.assertTrue(b_name.startswith("BUGCHECK_0x"))

    def test_add_sample(self):
        lines = []
        # Value None không được thêm
        agent.add_sample(lines, "metric_null", None)
        self.assertEqual(len(lines), 0)

        # Value số không nhãn
        agent.add_sample(lines, "metric_simple", 12.34)
        self.assertEqual(lines[-1], "metric_simple 12.34")

        # Value có nhãn
        agent.add_sample(lines, "metric_labeled", 5.6, {"host": "pc", "core": "0"})
        self.assertEqual(lines[-1], 'metric_labeled{core="0",host="pc"} 5.6')

    def test_ping_target_windows_parsing(self):
        # Case 1: Chuỗi ping Windows trả về time=XXms
        mock_output_1 = """
Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=42ms TTL=57
Reply from 1.1.1.1: bytes=32 time=39.5ms TTL=57

Ping statistics for 1.1.1.1:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 39ms, Maximum = 42ms, Average = 40ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_output_1)
            val = agent.ping_target_windows("1.1.1.1")
            self.assertEqual(val, 39.5)

        # Case 2: Chuỗi ping Windows tiếng Việt hoặc chỉ bắt được Minimum = XXms
        mock_output_2 = """
Thống kê Ping:
    Gói: Đã gửi = 2, Đã nhận = 2, Mất = 0 (0% mất mát),
Thời gian khứ hồi xấp xỉ tính bằng mili giây:
    Minimum = 41.2ms, Maximum = 45.0ms, Average = 43.1ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_output_2)
            val = agent.ping_target_windows("facebook.com")
            self.assertEqual(val, 41.2)

    def test_gpu_hotspot_delta_calculation(self):
        mock_gpus = [
            {
                "index": "0",
                "name": "NVIDIA GeForce RTX 3080",
                "temp_core": 72.0,
                "temp_hotspot": 98.5,
                "temp_mem": 86.0,
                "power_w": 320.5,
                "power_limit_w": 350.0,
                "fan_pct": 75.0,
                "util_gpu": 99.0,
                "mem_used": 8000000000,
                "mem_total": 10000000000,
                "clock_core": 1800,
                "clock_mem": 9500,
            }
        ]
        with mock.patch.object(agent, "collect_nvidia_gpu_metrics", return_value=[
            {**mock_gpus[0], "hotspot_delta": round(98.5 - 72.0, 2)}
        ]):
            gpus = agent.collect_nvidia_gpu_metrics()
            self.assertEqual(gpus[0]["hotspot_delta"], 26.5)

    def test_generate_prometheus_metrics_output(self):
        agent.slow_cache["last_reboot_unexpected"] = 1
        agent.slow_cache["whea_count"] = 2
        agent.slow_cache["last_crash_info"] = {
            "bugcheck_hex": "0x00000000",
            "bugcheck_name": "HARD_POWER_OFF_OR_THERMAL_TRIP",
            "bugcheck_desc": "Nguồn sụt áp hoặc quá nhiệt",
            "time": "2026-09-08 16:30:00",
        }
        agent.slow_cache["latencies"] = {
            "gateway": {"val": 0.8, "service": "Gateway", "category": "local_network"},
            "cloudflare": {"val": 38.5, "service": "Cloudflare (1.1.1.1)", "category": "global_dns"},
        }

        with mock.patch.object(agent, "collect_motherboard_and_cpu_power", return_value={
            "v12": 11.85, "v5": 5.02, "v33": 3.31, "cpu_power_w": 95.0, "cpu_temp_package": 68.5, "nvme_temps": {"Samsung 980 Pro": 45.0}
        }):
            with mock.patch.object(agent, "collect_system_resources", return_value={
                "cpu_percent_total": 25.0, "cpu_percent_cores": [20.0, 30.0], "ram_total": 16000000000, "ram_used": 8000000000,
                "ram_pct": 50.0, "pagefile_total": 20000000000, "pagefile_used": 10000000000, "pagefile_pct": 50.0,
                "uptime_sec": 3600, "disks": [{"mountpoint": "C:\\", "fstype": "NTFS", "total": 500000000000, "used": 200000000000, "free": 300000000000, "percent": 40.0}],
                "disk_read_bytes": 100, "disk_write_bytes": 200
            }):
                with mock.patch.object(agent, "collect_nvidia_gpu_metrics", return_value=[]):
                    metrics = agent.generate_prometheus_metrics()

                    # Kiểm tra các metric chính
                    self.assertIn("windows_last_reboot_unexpected 1", metrics)
                    self.assertIn("windows_whea_errors_total 2", metrics)
                    self.assertIn('windows_last_crash_info{bugcheck_desc="Nguồn sụt áp hoặc quá nhiệt",bugcheck_hex="0x00000000",bugcheck_name="HARD_POWER_OFF_OR_THERMAL_TRIP",time="2026-09-08 16:30:00"} 1', metrics)
                    self.assertIn('motherboard_voltage_volts{rail="12v"} 11.85', metrics)
                    self.assertIn('motherboard_voltage_volts{rail="5v"} 5.02', metrics)
                    self.assertIn("cpu_package_power_watts 95.0", metrics)
                    self.assertIn("cpu_package_temperature_celsius 68.5", metrics)
                    self.assertIn('nvme_temperature_celsius{disk="Samsung 980 Pro"} 45.0', metrics)
                    self.assertIn('system_disk_utilization_percent{drive="C:",fstype="NTFS"} 40.0', metrics)
                    self.assertIn('net_ping_latency_ms{category="local_network",service="Gateway",target="gateway"} 0.8', metrics)
                    self.assertIn('net_ping_latency_ms{category="global_dns",service="Cloudflare (1.1.1.1)",target="cloudflare"} 38.5', metrics)


if __name__ == "__main__":
    unittest.main()


import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import collect_sensors


VALID_CONFIG = """\
min_fan1_speed = 1600
max_fan1_speed = 2950
low_temp = 38
high_temp = 48
max_temp = 56
"""


class MbpfanConfigTests(unittest.TestCase):
    def test_parse_valid_config(self):
        values = collect_sensors.parse_mbpfan_config(VALID_CONFIG)

        self.assertEqual(values["low_temp"], 38.0)
        self.assertEqual(values["high_temp"], 48.0)
        self.assertEqual(values["max_temp"], 56.0)
        self.assertEqual(values["min_fan1_speed"], 1600.0)
        self.assertEqual(values["max_fan1_speed"], 2950.0)

    def test_parse_updated_mbpfan_profile(self):
        config_text = """
min_fan1_speed = 1300
max_fan1_speed = 2950
low_temp = 46
high_temp = 60
max_temp = 78
"""
        values = collect_sensors.parse_mbpfan_config(config_text)
        self.assertEqual(values["low_temp"], 46.0)
        self.assertEqual(values["high_temp"], 60.0)
        self.assertEqual(values["max_temp"], 78.0)
        self.assertEqual(values["min_fan1_speed"], 1300.0)
        self.assertEqual(values["max_fan1_speed"], 2950.0)

    def test_parse_rejects_missing_key(self):
        with self.assertRaises(ValueError):
            collect_sensors.parse_mbpfan_config(VALID_CONFIG.replace("max_temp = 56\n", ""))

    def test_parse_rejects_non_numeric_value(self):
        with self.assertRaises(ValueError):
            collect_sensors.parse_mbpfan_config(VALID_CONFIG.replace("high_temp = 48", "high_temp = hot"))

    def test_parse_rejects_invalid_threshold_order(self):
        with self.assertRaises(ValueError):
            collect_sensors.parse_mbpfan_config(VALID_CONFIG.replace("low_temp = 38", "low_temp = 60"))

    def test_parse_rejects_invalid_fan_speed_order(self):
        with self.assertRaises(ValueError):
            collect_sensors.parse_mbpfan_config(VALID_CONFIG.replace("min_fan1_speed = 1600", "min_fan1_speed = 4000"))

    def test_collect_valid_config_exports_thresholds_and_limits(self):
        path = self.write_config(VALID_CONFIG)
        lines = []

        self.assertTrue(collect_sensors.collect_mbpfan_config(lines, path))
        self.assertIn("thermal_mbpfan_config_valid 1", lines)
        self.assertIn(
            'thermal_mbpfan_temperature_threshold_celsius{threshold="high"} 48.0',
            lines,
        )
        self.assertIn(
            'thermal_mbpfan_fan_speed_limit_rpm{fan="1",limit="max"} 2950.0',
            lines,
        )

    def test_invalid_config_does_not_remove_sensor_metrics(self):
        path = self.write_config(VALID_CONFIG.replace("max_temp = 56\n", ""))
        lines = ["thermal_temperature_celsius{component=\"cpu\",sensor=\"package\"} 45.0"]

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(collect_sensors.collect_mbpfan_config(lines, path))

        self.assertIn(
            'thermal_temperature_celsius{component="cpu",sensor="package"} 45.0',
            lines,
        )
        self.assertIn("thermal_mbpfan_config_valid 0", lines)
        self.assertFalse(
            any("thermal_mbpfan_temperature_threshold_celsius" in line for line in lines)
        )

    def test_main_succeeds_with_sensor_data_when_config_is_invalid(self):
        path = self.write_config(VALID_CONFIG.replace("max_temp = 56\n", ""))
        sensor_json = '{"coretemp-isa-0000": {"Package id 0": {"temp1_input": 45.0}}}'
        written_lines = []

        with (
            mock.patch.object(collect_sensors, "MBPFAN_CONFIG", path),
            mock.patch.object(
                collect_sensors.subprocess,
                "run",
                return_value=mock.Mock(stdout=sensor_json),
            ),
            mock.patch.object(
                collect_sensors,
                "write_atomic",
                side_effect=lambda lines: written_lines.extend(lines),
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(collect_sensors.main(), 0)

        self.assertIn("thermal_collector_success 1", written_lines)
        self.assertIn(
            'thermal_temperature_celsius{component="cpu",sensor="package"} 45.0',
            written_lines,
        )
        self.assertIn("thermal_mbpfan_config_valid 0", written_lines)

    def setUp(self):
        collect_sensors._intake_history.clear()

    def test_ambient_estimation_with_ta0v(self):
        sensor_json = {
            "coretemp-isa-0000": {
                "Package id 0": {"temp1_input": 45.0},
            },
            "applesmc-isa-0300": {
                "Main": {"fan1_input": 2000.0},
                "TA0V ": {"temp1_input": 28.0},
                "TC0p": {"temp2_input": 42.0},
                "TG0p": {"temp3_input": 45.0},
            },
        }
        lines = []
        collect_sensors.collect_measurements(sensor_json, lines)
        self.assertIn('thermal_temperature_celsius{component="applesmc",sensor="air_intake"} 28.0', lines)
        self.assertIn('thermal_temperature_celsius{component="applesmc",sensor="cpu_proximity"} 42.0', lines)
        self.assertIn('thermal_temperature_celsius{component="applesmc",sensor="gpu_proximity"} 45.0', lines)
        self.assertIn("thermal_inlet_air_temperature_celsius 28.0", lines)
        # Fan 2000 RPM (offset 0.2), CPU 45°C (<=55, no penalty) -> ambient = 28.0 - 0.2 = 27.8
        self.assertIn("thermal_room_temperature_celsius 27.8", lines)
        self.assertIn("thermal_estimated_ambient_temperature_celsius 27.8", lines)

    def test_ambient_estimation_dynamic_low_fan_and_cpu_load(self):
        sensor_json = {
            "coretemp-isa-0000": {
                "Package id 0": {"temp1_input": 60.0},
            },
            "applesmc-isa-0300": {
                "Main": {"fan1_input": 1300.0},
                "TA0V": {"temp1_input": 28.0},
            },
        }
        lines = []
        collect_sensors.collect_measurements(sensor_json, lines)
        self.assertIn("thermal_inlet_air_temperature_celsius 28.0", lines)
        # Fan 1300 RPM (offset 0.4), CPU 60°C (penalty (60-55)*0.015 = 0.075) -> total offset ~0.48 -> ambient = 28.0 - 0.48 = 27.5
        self.assertIn("thermal_room_temperature_celsius 27.5", lines)
        self.assertIn("thermal_estimated_ambient_temperature_celsius 27.5", lines)

    def test_ambient_estimation_cool_intake(self):
        sensor_json = {
            "coretemp-isa-0000": {
                "Package id 0": {"temp1_input": 42.0},
            },
            "applesmc-isa-0300": {
                "Main": {"fan1_input": 1300.0},
                "TA0V": {"temp1_input": 20.8},
            },
        }
        lines = []
        collect_sensors.collect_measurements(sensor_json, lines)
        self.assertIn("thermal_inlet_air_temperature_celsius 20.8", lines)
        # Cool intake (20.8°C), Fan 1300 RPM (offset 0.4) -> room ambient = 20.8 - 0.4 = 20.4
        self.assertIn("thermal_room_temperature_celsius 20.4", lines)
        self.assertIn("thermal_estimated_ambient_temperature_celsius 20.4", lines)

    def test_intake_rate_of_change_gradient(self):
        t0 = 1000.0
        # First sample
        r0 = collect_sensors.calculate_intake_rate_of_change(t0, 24.0)
        self.assertEqual(r0, 0.0)
        # 60 seconds later, temp drops 1.2°C (rate = -1.2 °C/min)
        t1 = t0 + 60.0
        r1 = collect_sensors.calculate_intake_rate_of_change(t1, 22.8)
        self.assertAlmostEqual(r1, -1.2, places=2)
        # Staleness check: > 300s
        t2 = t1 + 350.0
        r2 = collect_sensors.calculate_intake_rate_of_change(t2, 22.0)
        self.assertEqual(r2, 0.0)

    def test_ambient_estimation_fallback_without_ta0v(self):
        sensor_json = {
            "applesmc-isa-0300": {
                "TC0p": {"temp1_input": 40.0},
                "TG0p": {"temp2_input": 48.0},
                "TL0p": {"temp3_input": 32.0},
            }
        }
        lines = []
        collect_sensors.collect_measurements(sensor_json, lines)
        # Min is 32.0, fallback ambient = 32.0 - 4.5 = 27.5
        self.assertIn("thermal_estimated_ambient_temperature_celsius 27.5", lines)

    def write_config(self, content):
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        self.addCleanup(lambda: os.unlink(temporary.name))
        with temporary:
            temporary.write(content)
        return temporary.name


if __name__ == "__main__":
    unittest.main()

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

    def write_config(self, content):
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        self.addCleanup(lambda: os.unlink(temporary.name))
        with temporary:
            temporary.write(content)
        return temporary.name


if __name__ == "__main__":
    unittest.main()

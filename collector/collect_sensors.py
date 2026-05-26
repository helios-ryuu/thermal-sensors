#!/usr/bin/env python3
"""Xuất các giá trị lm-sensors đã chọn theo định dạng Prometheus textfile."""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

OUTPUT = "/var/lib/thermal-sensors/textfile/thermal_sensors.prom"
MBPFAN_CONFIG = "/etc/mbpfan.conf"

MBPFAN_REQUIRED_KEYS = {
    "low_temp",
    "high_temp",
    "max_temp",
    "min_fan1_speed",
    "max_fan1_speed",
}

# Chỉ thêm key Apple SMC sau khi đã xác minh key đó ổn định trên đúng máy này.
APPLE_TEMPERATURE_ALLOWLIST = {
    # "TC0P": "cpu_proximity",
}


def prometheus_escape(value):
    """Thoát ký tự đặc biệt trong giá trị nhãn Prometheus."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def add_sample(lines, metric, value, component=None, sensor=None):
    """Thêm một mẫu metric, kèm nhãn cảm biến nếu được cung cấp."""
    labels = ""
    if component is not None and sensor is not None:
        labels = '{{component="{}",sensor="{}"}}'.format(
            prometheus_escape(component), prometheus_escape(sensor)
        )
    lines.append("{}{} {}".format(metric, labels, value))


def add_labels_sample(lines, metric, value, labels):
    """Thêm một mẫu metric với tập nhãn bất kỳ."""
    rendered_labels = ",".join(
        '{}="{}"'.format(name, prometheus_escape(label_value))
        for name, label_value in labels.items()
    )
    lines.append("{}{{{}}} {}".format(metric, rendered_labels, value))


def find_chip(data, prefix):
    """Tìm chip theo tiền tố tên mà `sensors -j` xuất ra."""
    for chip_name, chip_values in data.items():
        if chip_name.startswith(prefix) and isinstance(chip_values, dict):
            return chip_values
    return {}


def find_feature(chip, feature_name):
    """Tìm feature, bỏ qua khoảng trắng cuối trong nhãn Apple SMC."""
    for name, value in chip.items():
        if name.strip() == feature_name and isinstance(value, dict):
            return value
    return {}


def read_feature(chip, feature_name, suffixes):
    """Đọc giá trị số đầu tiên phù hợp với feature và hậu tố trường."""
    feature = find_feature(chip, feature_name)
    for field_name, field_value in feature.items():
        if field_name.endswith(tuple(suffixes)) and isinstance(field_value, (int, float)):
            value = float(field_value)
            if math.isfinite(value):
                return value
    return None


def add_temperature(lines, component, sensor, value):
    """Chỉ xuất dải nhiệt độ hợp lệ; loại sentinel và số âm quan sát được."""
    if value is not None and 0.0 <= value < 125.0:
        add_sample(lines, "thermal_temperature_celsius", value, component, sensor)


def add_nonnegative(lines, metric, component, sensor, value):
    """Chỉ xuất các giá trị tốc độ/công suất không âm."""
    if value is not None and value >= 0.0:
        add_sample(lines, metric, value, component, sensor)


def parse_mbpfan_config(content):
    """Đọc và kiểm tra các giới hạn cấu hình mbpfan cần quan sát."""
    config = {}
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue

        key, raw_value = (piece.strip() for piece in line.split("=", 1))
        if key not in MBPFAN_REQUIRED_KEYS:
            continue

        try:
            value = float(raw_value)
        except ValueError as error:
            raise ValueError("Giá trị {} không phải số.".format(key)) from error
        if not math.isfinite(value):
            raise ValueError("Giá trị {} không hữu hạn.".format(key))
        config[key] = value

    missing = sorted(MBPFAN_REQUIRED_KEYS - set(config))
    if missing:
        raise ValueError("Thiếu cấu hình: {}.".format(", ".join(missing)))
    if not config["low_temp"] <= config["high_temp"] <= config["max_temp"]:
        raise ValueError("Thứ tự ngưỡng nhiệt độ mbpfan không hợp lệ.")
    if not config["min_fan1_speed"] <= config["max_fan1_speed"]:
        raise ValueError("Thứ tự giới hạn tốc độ quạt mbpfan không hợp lệ.")
    return config


def collect_mbpfan_config(lines, path=MBPFAN_CONFIG):
    """Xuất cấu hình mbpfan khi file đọc được và có giá trị hợp lệ."""
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            config = parse_mbpfan_config(config_file.read())
    except (OSError, ValueError) as error:
        add_sample(lines, "thermal_mbpfan_config_valid", 0)
        print("Đọc cấu hình mbpfan thất bại: {}".format(error), file=sys.stderr)
        return False

    add_sample(lines, "thermal_mbpfan_config_valid", 1)
    for key, threshold in (
        ("low_temp", "low"),
        ("high_temp", "high"),
        ("max_temp", "max"),
    ):
        add_labels_sample(
            lines,
            "thermal_mbpfan_temperature_threshold_celsius",
            config[key],
            {"threshold": threshold},
        )
    for key, limit in (("min_fan1_speed", "min"), ("max_fan1_speed", "max")):
        add_labels_sample(
            lines,
            "thermal_mbpfan_fan_speed_limit_rpm",
            config[key],
            {"fan": "1", "limit": limit},
        )
    return True


def collect_measurements(data, lines):
    """Ánh xạ những cảm biến hữu ích thành tập metric ổn định."""
    cpu = find_chip(data, "coretemp-")
    gpu = find_chip(data, "amdgpu-")
    pch = find_chip(data, "pch_cannonlake-")
    nvme = find_chip(data, "nvme-")
    apple = find_chip(data, "applesmc-")

    add_temperature(lines, "cpu", "package", read_feature(cpu, "Package id 0", ("_input",)))
    for feature_name in sorted(cpu):
        match = re.fullmatch(r"Core ([0-9]+)", feature_name.strip())
        if match:
            add_temperature(
                lines,
                "cpu",
                "core_{}".format(match.group(1)),
                read_feature(cpu, feature_name.strip(), ("_input",)),
            )

    add_temperature(lines, "gpu", "edge", read_feature(gpu, "edge", ("_input",)))
    add_nonnegative(
        lines,
        "thermal_gpu_power_watts",
        "gpu",
        "ppt",
        read_feature(gpu, "PPT", ("_average", "_input")),
    )
    add_temperature(lines, "pch", "temp1", read_feature(pch, "temp1", ("_input",)))
    add_temperature(lines, "nvme", "composite", read_feature(nvme, "Composite", ("_input",)))
    add_nonnegative(
        lines,
        "thermal_fan_speed_rpm",
        "fan",
        "main",
        read_feature(apple, "Main", ("_input",)),
    )

    for raw_sensor, sensor_name in APPLE_TEMPERATURE_ALLOWLIST.items():
        add_temperature(
            lines,
            "applesmc",
            sensor_name,
            read_feature(apple, raw_sensor, ("_input",)),
        )


def write_atomic(lines):
    """Thay thế file metric nguyên tử để exporter không đọc file dở dang."""
    directory = os.path.dirname(OUTPUT)
    fd, temporary_path = tempfile.mkstemp(prefix=".thermal_sensors.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, OUTPUT)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def main():
    """Thu thập một lần và luôn ghi trạng thái của lần thử gần nhất."""
    now = time.time()
    lines = [
        "# HELP thermal_collector_success Lần thu thập gần nhất có thành công hay không.",
        "# TYPE thermal_collector_success gauge",
        "# HELP thermal_collector_timestamp_seconds Thời điểm Unix của lần thu thập gần nhất.",
        "# TYPE thermal_collector_timestamp_seconds gauge",
        "# HELP thermal_mbpfan_config_valid Cấu hình mbpfan có đọc và kiểm tra hợp lệ hay không.",
        "# TYPE thermal_mbpfan_config_valid gauge",
        "# HELP thermal_mbpfan_temperature_threshold_celsius Các ngưỡng nhiệt độ trong cấu hình mbpfan.",
        "# TYPE thermal_mbpfan_temperature_threshold_celsius gauge",
        "# HELP thermal_mbpfan_fan_speed_limit_rpm Các giới hạn tốc độ quạt trong cấu hình mbpfan.",
        "# TYPE thermal_mbpfan_fan_speed_limit_rpm gauge",
    ]
    exit_code = 0
    try:
        completed = subprocess.run(
            ["sensors", "-j"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        data = json.loads(completed.stdout)
        lines.extend(
            [
                "# HELP thermal_temperature_celsius Các giá trị nhiệt độ phần cứng đã chọn.",
                "# TYPE thermal_temperature_celsius gauge",
                "# HELP thermal_fan_speed_rpm Các giá trị tốc độ quạt đã chọn.",
                "# TYPE thermal_fan_speed_rpm gauge",
                "# HELP thermal_gpu_power_watts Các giá trị công suất GPU đã chọn.",
                "# TYPE thermal_gpu_power_watts gauge",
            ]
        )
        add_sample(lines, "thermal_collector_success", 1)
        add_sample(lines, "thermal_collector_timestamp_seconds", now)
        collect_measurements(data, lines)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as error:
        add_sample(lines, "thermal_collector_success", 0)
        add_sample(lines, "thermal_collector_timestamp_seconds", now)
        print("Thu thập cảm biến nhiệt độ thất bại: {}".format(error), file=sys.stderr)
        exit_code = 1

    collect_mbpfan_config(lines, MBPFAN_CONFIG)
    write_atomic(lines)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

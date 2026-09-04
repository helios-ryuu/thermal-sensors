#!/usr/bin/env python3
"""Xuất các giá trị lm-sensors đã chọn theo định dạng Prometheus textfile."""

import argparse
import collections
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

OUTPUT = os.environ.get("TEXTFILE_OUTPUT_PATH", "/textfile/thermal_sensors.prom")
MBPFAN_CONFIG = os.environ.get("MBPFAN_CONFIG_PATH", "/etc/mbpfan.conf")

MBPFAN_REQUIRED_KEYS = {
    "low_temp",
    "high_temp",
    "max_temp",
    "min_fan1_speed",
    "max_fan1_speed",
}

# Cảm biến Apple SMC cho phép theo dõi (hỗ trợ case-insensitive)
APPLE_TEMPERATURE_ALLOWLIST = {
    "TA0V": "air_intake",
    "TC0p": "cpu_proximity",
    "TG0p": "gpu_proximity",
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
    """Tìm feature, bỏ qua khoảng trắng cuối và không phân biệt hoa thường."""
    target = feature_name.strip().upper()
    for name, value in chip.items():
        if name.strip().upper() == target and isinstance(value, dict):
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


def collect_mbpfan_config(lines, path=None):
    """Xuất cấu hình mbpfan khi file đọc được và có giá trị hợp lệ."""
    target_path = path or MBPFAN_CONFIG
    try:
        with open(target_path, "r", encoding="utf-8") as config_file:
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


_intake_history = collections.deque(maxlen=20)


def calculate_intake_rate_of_change(now, current_temp):
    """Tính toán tốc độ biến thiên nhiệt độ nạp dT/dt (°C / phút)."""
    if not _intake_history:
        _intake_history.append((now, current_temp))
        return 0.0
    old_time, old_temp = _intake_history[0]
    dt_sec = now - old_time
    if dt_sec > 300.0:
        _intake_history.clear()
        _intake_history.append((now, current_temp))
        return 0.0
    _intake_history.append((now, current_temp))
    if dt_sec >= 10.0:
        return (current_temp - old_temp) / (dt_sec / 60.0)
    return 0.0


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

    # --- ĐO NHIỆT ĐỘ KHÍ NẠP & ƯỚC TÍNH NHIỆT ĐỘ PHÒNG ĐỘNG HỌC ---
    air_intake = read_feature(apple, "TA0V", ("_input",))
    fan_speed = read_feature(apple, "Main", ("_input",))
    cpu_pkg = read_feature(cpu, "Package id 0", ("_input",))
    gpu_temp = read_feature(gpu, "edge", ("_input",))

    inlet_air = None
    ambient = None

    if air_intake is not None and 0.0 < air_intake < 125.0:
        inlet_air = round(air_intake, 1)
        now_ts = time.time()
        dt_rate = calculate_intake_rate_of_change(now_ts, air_intake)

        # 1. Quán tính trễ vi phân (Thermal inertia of thermistor sensor: tau ~ 0.5 min)
        # Giới hạn chặt: [-0.3°C, +0.3°C]
        gradient_adj = max(-0.3, min(0.3, 0.5 * dt_rate))

        # 2. Phân tầng khí lạnh theo độ cao (Cold air stratification / buoyancy)
        # Khí lạnh điều hòa chìm sát mặt bàn, không khí ngang tầm người ngồi cao hơn 0.0 - 0.4°C
        if air_intake <= 21.5:
            strat_adj = 0.4
        elif air_intake >= 23.5:
            strat_adj = 0.0
        else:
            strat_adj = 0.4 * ((23.5 - air_intake) / 2.0)

        # 3. Bù trừ nhiệt dẫn qua khung vỏ khi máy tải rất nặng (CPU/GPU > 55°C)
        load_penalty = 0.0
        if cpu_pkg and cpu_pkg > 55.0:
            load_penalty += (cpu_pkg - 55.0) * 0.01
        if gpu_temp and gpu_temp > 55.0:
            load_penalty += (gpu_temp - 55.0) * 0.01
        load_penalty = min(0.2, load_penalty)

        # Tổng hợp offset động, kẹp cứng tuyệt đối trong [-0.6°C, +0.6°C] (không vượt quá 1°C)
        delta_dynamic = max(-0.6, min(0.6, gradient_adj + strat_adj - load_penalty))
        ambient = round(air_intake + delta_dynamic, 1)
    else:
        # Fallback an toàn nếu mất cảm biến TA0V
        candidates = []
        for fname, fval in apple.items():
            if isinstance(fval, dict):
                for k, v in fval.items():
                    if k.endswith("_input") and isinstance(v, (int, float)):
                        val = float(v)
                        if 15.0 <= val <= 60.0:
                            candidates.append(val)
        if candidates:
            ambient = round(min(candidates) - 4.5, 1)
            inlet_air = ambient

    if inlet_air is not None and 0.0 <= inlet_air < 100.0:
        add_sample(lines, "thermal_inlet_air_temperature_celsius", inlet_air)
    if ambient is not None and 0.0 <= ambient < 100.0:
        add_sample(lines, "thermal_room_temperature_celsius", ambient)
        add_sample(lines, "thermal_estimated_ambient_temperature_celsius", ambient)

def write_atomic(lines, output_path=None):
    """Thay thế file metric nguyên tử để exporter không đọc file dở dang."""
    target_path = output_path or OUTPUT
    directory = os.path.dirname(target_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".thermal_sensors.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def collect_once(output_path=None, config_path=None):
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
                "# HELP thermal_inlet_air_temperature_celsius Nhiệt độ khí nạp trực tiếp vào đáy iMac từ cảm biến TA0V.",
                "# TYPE thermal_inlet_air_temperature_celsius gauge",
                "# HELP thermal_room_temperature_celsius Nhiệt độ phòng ước tính qua mô hình nhiệt động học (Dynamic Thermal Gradient).",
                "# TYPE thermal_room_temperature_celsius gauge",
                "# HELP thermal_estimated_ambient_temperature_celsius Nhiệt độ phòng ước tính qua mô hình bù trừ nhiệt động học (Dynamic Thermal Gradient).",
                "# TYPE thermal_estimated_ambient_temperature_celsius gauge",
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

    collect_mbpfan_config(lines, config_path or MBPFAN_CONFIG)
    if output_path is not None:
        write_atomic(lines, output_path)
    else:
        write_atomic(lines)
    return exit_code


def main(argv=None):
    """Hàm chạy chính hỗ trợ cả chạy một lần (oneshot) và chạy lặp (loop)."""
    parser = argparse.ArgumentParser(description="Thermal sensors Prometheus textfile collector")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Chạy liên tục theo chu kỳ",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("COLLECTOR_INTERVAL", "10")),
        help="Chu kỳ thu thập tính bằng giây (mặc định: 10)",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Chỉ chạy một lần rồi thoát",
    )
    args = parser.parse_args(argv if argv is not None else [])

    run_in_loop = (args.loop or "COLLECTOR_INTERVAL" in os.environ) and not args.oneshot

    if not run_in_loop:
        return collect_once()

    stop = False

    def handle_signal(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    interval = max(1, args.interval)
    print("Bắt đầu collector chạy liên tục mỗi {}s...".format(interval), flush=True)

    while not stop:
        start_time = time.time()
        collect_once()
        elapsed = time.time() - start_time
        sleep_time = max(0.1, interval - elapsed)

        end_sleep = time.time() + sleep_time
        while not stop and time.time() < end_sleep:
            time.sleep(min(0.5, max(0.01, end_sleep - time.time())))

    print("Collector đã dừng an toàn.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

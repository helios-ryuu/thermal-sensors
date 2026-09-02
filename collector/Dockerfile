FROM python:alpine

# Cài đặt lm-sensors để đọc cảm biến phần cứng qua /sys
RUN apk add --no-cache lm-sensors

WORKDIR /app

COPY collect_sensors.py /app/collect_sensors.py

ENV TEXTFILE_OUTPUT_PATH=/textfile/thermal_sensors.prom \
    MBPFAN_CONFIG_PATH=/etc/mbpfan.conf \
    COLLECTOR_INTERVAL=10 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/app/collect_sensors.py"]


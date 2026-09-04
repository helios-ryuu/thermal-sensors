FROM python:alpine

# Cài đặt các công cụ mạng nhẹ để thu thập topology, iptables và traceroute
RUN apk add --no-cache \
    iproute2 \
    iptables \
    curl \
    traceroute \
    bind-tools

WORKDIR /app

COPY collect_network.py /app/collect_network.py

ENV METRICS_OUTPUT_PATH=/textfile/network_metrics.prom \
    COLLECTOR_FAST_INTERVAL=15 \
    COLLECTOR_SLOW_INTERVAL=120 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/app/collect_network.py"]


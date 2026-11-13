FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/log-collector COLLECTOR_STATE=/data/collector.sqlite3 COLLECTOR_CONFIG=/config/collector.json
WORKDIR /app
RUN useradd --uid 10001 --create-home app && mkdir /data /config && chown app:app /data
COPY shared /app/shared
COPY services/log-collector/log_collector /app/services/log-collector/log_collector
USER 10001
HEALTHCHECK --interval=20s --timeout=5s CMD python -m log_collector health
# File collection only in containers; journal collection runs on the host.
ENTRYPOINT ["python", "-m", "log_collector"]
CMD ["collect"]

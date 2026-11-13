FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/alert-manager WORKER_HEARTBEAT_PATH=/state/worker-health.json
WORKDIR /app
COPY infrastructure/docker/requirements-pipeline.txt /tmp/infrastructure/docker/requirements-pipeline.txt
COPY services/alert-manager/requirements.txt /tmp/services/alert-manager/requirements.txt
RUN pip install --no-cache-dir -r /tmp/services/alert-manager/requirements.txt && useradd --uid 10001 --create-home app && mkdir /state && chown app:app /state
COPY shared /app/shared
COPY services/alert-manager/alert_manager /app/services/alert-manager/alert_manager
COPY infrastructure/postgres /app/infrastructure/postgres
COPY scripts/manage_pipeline.py /app/scripts/manage_pipeline.py
USER 10001
EXPOSE 8014
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8014/health/ready', timeout=3)"
ENTRYPOINT ["python", "-m", "alert_manager"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8014"]

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/ml-inference MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ENABLE_TELEMETRY=false
WORKDIR /app
COPY services/ml-training/requirements.txt services/ml-training/requirements-mlflow.txt /tmp/services/ml-training/
COPY services/ml-inference/requirements.txt /tmp/services/ml-inference/requirements.txt
COPY infrastructure/docker/requirements-pipeline.txt /tmp/infrastructure/docker/requirements-pipeline.txt
RUN pip install --no-cache-dir -r /tmp/services/ml-inference/requirements.txt && useradd --uid 10001 --create-home app && mkdir /audit /state && chown app:app /audit /state
COPY shared /app/shared
COPY ml/__init__.py ml/model.py /app/ml/
COPY ml/features /app/ml/features
ENV WORKER_HEARTBEAT_PATH=/state/worker-health.json
COPY services/ml-inference/ml_inference /app/services/ml-inference/ml_inference
USER 10001
EXPOSE 8013
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8013/health/ready', timeout=3)"
CMD ["uvicorn", "ml_inference.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8013", "--workers", "1", "--limit-concurrency", "32", "--timeout-keep-alive", "5", "--no-access-log"]

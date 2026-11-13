FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/api-gateway GATEWAY_HOST=0.0.0.0 MLFLOW_ENABLE_TELEMETRY=false
WORKDIR /app
COPY infrastructure/docker/requirements-pipeline.txt /tmp/infrastructure/docker/requirements-pipeline.txt
COPY services/api-gateway/requirements.txt /tmp/services/api-gateway/requirements.txt
RUN pip install --no-cache-dir -r /tmp/services/api-gateway/requirements.txt && useradd --uid 10001 --create-home app && mkdir /state && chown app:app /state
COPY shared /app/shared
COPY services/api-gateway/api_gateway /app/services/api-gateway/api_gateway
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"
ENTRYPOINT ["python", "-m", "api_gateway"]

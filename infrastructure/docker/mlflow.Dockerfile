FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ENABLE_TELEMETRY=false
WORKDIR /app
COPY services/ml-training/requirements.txt services/ml-training/requirements-mlflow.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-mlflow.txt && useradd --uid 10001 --create-home app && mkdir /mlflow && chown app:app /mlflow
USER 10001
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)"
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", "--workers", "1", "--backend-store-uri", "sqlite:////mlflow/tracking.db", "--artifacts-destination", "/mlflow/artifacts", "--serve-artifacts", "--allowed-hosts", "localhost:*,127.0.0.1:*,mlflow:5000", "--cors-allowed-origins", "http://localhost:5000,http://127.0.0.1:5000"]

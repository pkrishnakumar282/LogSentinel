FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/ml-training MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ENABLE_TELEMETRY=false
WORKDIR /app
COPY services/ml-training/requirements.txt services/ml-training/requirements-mlflow.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-mlflow.txt && useradd --uid 10001 --create-home app && mkdir /data /artifacts /audit && chown app:app /data /artifacts /audit
COPY shared /app/shared
COPY ml/__init__.py ml/model.py ml/mlflow_model.py /app/ml/
COPY ml/features /app/ml/features
COPY services/ml-training/ml_training /app/services/ml-training/ml_training
COPY services/ml-training/requirements.txt services/ml-training/requirements-mlflow.txt /app/services/ml-training/
USER 10001
ENTRYPOINT ["python", "-m", "ml_training"]
CMD ["--help"]

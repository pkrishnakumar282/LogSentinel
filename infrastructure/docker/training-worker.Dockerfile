FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/ml-training MLFLOW_ENABLE_TELEMETRY=false MLFLOW_DISABLE_AGENT_HINT=1 TRAINING_JOB_ROOT=/jobs
WORKDIR /app
COPY services/ml-training/requirements.txt services/ml-training/requirements-mlflow.txt services/ml-training/requirements-worker.txt /tmp/services/ml-training/
COPY infrastructure/docker/requirements-pipeline.txt /tmp/infrastructure/docker/requirements-pipeline.txt
RUN pip install --no-cache-dir -r /tmp/services/ml-training/requirements-worker.txt && useradd --uid 10001 --create-home app && mkdir /jobs && chown app:app /jobs
COPY shared /app/shared
COPY ml /app/ml
COPY services/ml-training/ml_training /app/services/ml-training/ml_training
USER 10001
ENTRYPOINT ["python", "-m", "ml_training.job_worker"]
CMD ["--once"]

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/ml-training
WORKDIR /app
COPY services/ml-training/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 10001 --create-home app && mkdir /data /artifacts && chown app:app /data /artifacts
COPY shared /app/shared
COPY ml/__init__.py ml/model.py /app/ml/
COPY ml/features /app/ml/features
COPY services/ml-training/ml_training /app/services/ml-training/ml_training
USER 10001
# One-shot job; no long-running web server or continuous resource consumption.
ENTRYPOINT ["python", "-m", "ml_training"]
CMD ["--help"]

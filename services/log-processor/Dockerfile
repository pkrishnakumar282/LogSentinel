FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/services/log-processor
WORKDIR /app
COPY services/log-processor/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 10001 --create-home app
COPY shared /app/shared
COPY services/log-processor/log_processor /app/services/log-processor/log_processor
RUN mkdir /data && chown app:app /data
USER 10001
EXPOSE 8012
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/health/ready', timeout=3)"
CMD ["uvicorn", "log_processor.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8012", "--no-access-log"]

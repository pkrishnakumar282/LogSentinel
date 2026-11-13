# Alert manager

Consumes durable prediction notices, creates anomaly-only alerts, groups recurring patterns,
and applies audited acknowledgement/resolution. The FastAPI API, stream consumer and outbox
relay are independently runnable modes. No external email/Slack notification is sent.

See [stream and alert setup](../../docs/streams-and-alerts.md) for dependencies, migrations,
configuration, API examples and recovery contracts. Install `requirements.txt` and add the
repository root and `services/alert-manager` to `PYTHONPATH`. PostgreSQL is the worker
default; SQLite is an explicit development option. The Dockerfile runs as UID 10001.

SQLAlchemy/SQLite and ASGI behavior was tested locally; live Redis/PostgreSQL and Docker
startup remain unverified. Durable business receipts, not Redis transport alone, prevent
duplicate alert occurrences.

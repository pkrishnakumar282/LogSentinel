# log-processor

Parses, validates and redacts Linux, Nginx and Python records, mines templates and publishes normalized events.

See [ingestion setup and API documentation](../../docs/ingestion.md) for commands,
configuration, supported formats and runtime limitations.

Build from the repository root with this directory's Dockerfile; shared contracts are
copied from `shared/`. No root application process or Docker socket is required.

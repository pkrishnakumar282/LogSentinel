# log-collector

Collects files and the user journal with durable cursors, bounded HTTP retries and explicit synthetic generation.

See [ingestion setup and API documentation](../../docs/ingestion.md) for commands,
configuration, supported formats and runtime limitations.

Build from the repository root with this directory's Dockerfile; shared contracts are
copied from `shared/`. No root application process or Docker socket is required.

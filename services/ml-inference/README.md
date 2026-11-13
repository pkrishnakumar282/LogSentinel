# ML inference service

Loads a specific approved MLflow version, verifies audit/package/runtime evidence, and
scores normalized logs with shared fitted preprocessing and transactional causal contexts.
Provides authenticated single/batch APIs, liveness/readiness, model health, safe reloads
and local CLI scoring. It imports no training service business code.

See [setup, API examples and operating limits](../../docs/inference.md). Install
`requirements.txt` from the monorepo root environment and add the root and
`services/ml-inference` to `PYTHONPATH`. The Dockerfile uses a non-root Python 3.12 process;
`compose.inference.yml` adds the optional service alongside `compose.mlflow.yml`.

The current persisted candidate failed Phase 4 gates, so default inference remains unready.
No unapproved local candidate fallback exists. Phase 6 now adds the `worker` command with durable prediction/checkpoint/outbox commits
and restart restoration. See [stream operations](../../docs/streams-and-alerts.md).
Full gateway/application integration remains Phase 7. Actual ML/ASGI tests run on Python 3.14; socket/container execution
and the compiled stack on Python 3.12 remain validation gates.

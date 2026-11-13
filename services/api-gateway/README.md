# API Gateway

Frontend-facing FastAPI REST service on localhost:8000. SQL repositories provide log,
dashboard, source, training-job and audit reads; bounded authenticated clients forward
processor/inference/alert operations to the owning services. MLflow metadata is read-only.

See [setup, API contracts, asynchronous jobs and retention](../../docs/gateway-and-database.md).

```bash
export PYTHONPATH="$PWD:$PWD/services/api-gateway"
python -m api_gateway
```

Install this service's requirements, configure environment tokens and migrate the database
first. No production schema creation, model approval, training or artifact loading occurs
inside the gateway.

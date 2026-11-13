#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
exec docker compose \
  -f docker-compose.yml \
  -f compose.ingestion.yml \
  -f compose.mlflow.yml \
  -f compose.streams.yml \
  -f compose.inference.yml \
  -f compose.gateway.yml \
  -f compose.frontend.yml \
  "$@"

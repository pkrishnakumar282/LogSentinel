# LogSentinel dashboard

Phase 8 adds the React + TypeScript operations UI in `frontend/`. It consumes the Phase 7
versioned API and renders observed database/MLflow/service data. No browser bundle contains
service credentials or invented metric samples.

## Start on Arch Linux

Use [gateway setup](gateway-and-database.md) to install dependencies, configure secrets,
apply Alembic migrations and start the backend/archive services first. Install a current LTS
Node version meeting the pinned Vite engine requirement (Node 22.12 or newer), then run:

```bash
cd frontend
npm install
cp --no-clobber .env.example .env
npm run dev
```

Vite is bound to `127.0.0.1:5173`. Its same-origin development proxy forwards API requests
to `http://127.0.0.1:8000`; set `VITE_GATEWAY_ORIGIN` in `frontend/.env` for another local
address. Production static hosting should reverse proxy `/api/v1` and `/health` to the
gateway, or use `VITE_API_BASE_URL` and allow the browser's origin through the gateway CORS
configuration. Keep this application on a trusted local origin.

On first open, enter `GATEWAY_API_TOKEN` from `.env`. Provide `ADMIN_API_TOKEN` to acknowledge
or resolve alerts and queue model training. The audit actor field is recorded with actions.
Credentials are held in tab-scoped session storage and are only sent to the configured API
base URL. Disconnect clears those values. Set Vite's environment before building; variables
prefixed with `VITE_` are compiled into the public asset and must never contain secrets.

## Dashboard pages

- **Overview** shows database totals, latest persisted predictions, source distribution,
  event-time volume, recent records and the inference model health response. Summary counts
  use each record's latest prediction, so multiple versions do not inflate counts.
- **Log explorer** supports message search, source/service/severity/time filters, pagination,
  manual or 15-second refresh, and a detail drawer with prediction versions and thresholds.
- **Anomalies** filters the same real event query to records whose latest prediction is flagged.
- **Alerts** filters and pages real grouped alerts, opens alert history, and offers audited
  acknowledge/resolve actions to administrators.
- **Model management** displays real MLflow registry versions, aliases, validation metadata,
  approved metrics and observed inference bindings. The asynchronous training form creates a
  durable job snapshot; the one-shot Docker Compose training worker must be run separately.
  Training diagnostics remain labeled as unlabeled holdout diagnostics, not evaluation scores.
- **Experiments** reads MLflow runs and charts precision/recall/F1 only where those labels
  were independently recorded. It does not derive or invent supervised performance metrics.
- **Data sources** displays source/host receipt counts and the first/last persisted event
  time. Recent/stale is a database receipt signal, not a collector connectivity probe.
- **System health** reads configured service readiness, Postgres state and Redis retained
  stream lengths. It also displays browser-measured gateway round-trip time and API process
  CPU/peak RSS, clearly scoped to those readings.

All views have loading, error or empty states. Missing model or registry services produce a
visible dependency error. Anomaly scores are shown as unusualness scores, never probabilities.
The system endpoint does not expose Prometheus time series, container resource usage or
Redis consumer backlog; those remain a later platform phase.

## Development checks

```bash
npm test
npm run build
```

The component tests cover log rendering/selection, severity, unscored state and retry/empty
states. The production build runs strict TypeScript project checks before Vite emits
`dist/`. Run `npm run dev` against a running gateway to exercise actual integration; the
frontend repository currently includes no captured screenshots. Compose frontend image
serving is part of Phase 9.

The dashboard package versions were pinned from the official React release page, Vite
release/package pages, the React Router changelog and Tailwind's release notes checked during
implementation. See [React versions](https://react.dev/versions), [Vite releases](https://vite.dev/releases),
[React Router changelog](https://reactrouter.com/changelog) and [Tailwind v4.3](https://tailwindcss.com/blog/tailwindcss-v4-3).

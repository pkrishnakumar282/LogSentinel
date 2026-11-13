# Phase 8 validation record

Phase 8 frontend implementation and local checks completed on 2026-11-13.

## Executed checks

In `frontend/`, with Node v26.13.1 and npm 12.2.2:

- `npm ci --no-audit --no-fund` completed from the committed npm lockfile (183 packages).
- `npm test` passed: 1 test file, 3 component tests.
- `npm run build` passed: strict TypeScript project checks and Vite production build.
- The initial production bundle exceeded Vite's 500 kB warning threshold. Route-based lazy loading was added; the rebuilt entry bundle is 338.64 kB (110.77 kB gzip), and the largest route/chart chunk is 317.63 kB (94.05 kB gzip). No chunk-size warning remains.
- During the Phase 7 API metadata work consumed by the dashboard, 13 gateway tests and four application metadata tests passed (17 total). See the Phase 7 validation record for the broader backend test status.

## Scope and remaining validation

The component tests cover log rendering and selection, unscored predictions, and loading/error/empty states. The dashboard pages call the versioned Phase 7 gateway endpoints and are documented in [the frontend guide](frontend.md).

This phase did not start a complete PostgreSQL/Redis/MLflow-backed stack or perform a browser-to-live-gateway end-to-end run. Those checks depend on the integrated stack and are part of the Docker and end-to-end work in later phases. The system page also makes unavailable metrics explicit; the gateway does not currently expose Prometheus time series, container resource usage, or Redis consumer backlog. Compose frontend image serving follows in Phase 9.

To repeat the frontend checks on Arch Linux:

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

The UI requires a running local API gateway and a valid token for live data. An approved model is required for production inference health and predictions; the default model remains unapproved.

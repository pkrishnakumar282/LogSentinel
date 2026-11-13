# Arch Linux environment

## Prerequisites

```bash
sudo pacman -Syu --needed git docker docker-compose nodejs-lts-krypton npm python uv openssl
sudo systemctl enable --now docker
```

Arch is rolling release: confirm the Node package resolves to Node 26 using `node --version`.
Use an LTS version manager if that package is unavailable in your configured repositories.
The [official Node download page](https://nodejs.org/en/download) identifies current LTS.
Python 3.12 is deliberately isolated from Arch's system Python; do not replace `/usr/bin/python`.
[Python 3.12 remains security-supported through October 2028](https://www.python.org/downloads/release/python-31216/).

```bash
uv python install 3.12
uv venv --python 3.12
source .venv/bin/activate
python --version
node --version
```

No application dependencies are required in Phase 1. Service-specific pinned dependencies
will arrive with implementation. Do not install project packages globally with pip.

Docker must be accessible to the user executing Compose. Prefer a configured rootless
Docker installation where available. The conventional system-daemon alternative is:

```bash
sudo usermod -aG docker "$USER"
# Log out and back in, then verify:
docker info
```

Membership in the docker group grants host-level control; make this host configuration
choice yourself. The application does not need root, privileged mode or a mounted Docker
socket. The coding session cannot change host group membership or daemon permissions.

## Configuration

Copy `.env.example` to `.env` and generate independent secrets using `openssl rand -hex 24`.
The required Phase 1 secrets are `POSTGRES_PASSWORD` and `REDIS_PASSWORD`. Admin API and
Grafana credentials become required when those services are introduced. Never use the
example file as a secret store. Compose interpolates `.env`; Python services will receive
explicit environment mappings when implemented.

The template documents stream names, retry controls, model alias, seed, alert grouping,
retention, ports and journal opt-in. All public ports will bind to localhost. Existing
infrastructure uses a private internal network and publishes no ports.

## Journal access

The collector will run as the host user using `journalctl --user` or journal records already
accessible to that user. System-wide access is optional and may require a host administrator
to grant journal group access. No root application process is required. Never expose broad
authentication logs through the dashboard. Journal collection is disabled by default.

## Checks and troubleshooting

Run `python scripts/preflight.py`; exit code 1 lists unmet prerequisites without changing
the host. `python3.12` may require activation of a uv-managed environment or adding its bin
directory to PATH; the installed Arch `python` can remain 3.14.

- Docker permission denied: configure rootless Docker or your host's daemon permissions,
  then start a new login session. Do not make the socket world-writable.
- Missing required Compose variable: fill the corresponding value in `.env`.
- No frontend/gateway port: these services remain scheduled. Phase 2 offers a processor
  endpoint on port 8012 when started explicitly; see [ingestion](ingestion.md).
- Images unavailable: check daemon connectivity and registry access; no pulls are assumed.
- Database credentials rejected after edits: existing volumes retain their original role
  credentials. Rotate explicitly rather than deleting data.
- Restricted workspace `.git`: this environment provides a read-only metadata directory;
  Git initialization is left to a normal writable checkout. Project files remain usable.

## Phase 1 validation record

See the phase completion message for executed check results. Container startup must be
validated on a host session with Docker daemon access before claiming infrastructure health.

Executed in the initial workspace on 2026-11-13:

- Standard-library foundation suite: 3 tests passed.
- Python compilation of scripts and unit tests: passed on Python 3.14.10.
- `docker compose config --quiet`: passed using explicitly supplied non-secret validation
  values; no `.env` or credentials were generated.
- Preflight: Node 26.13.1, npm 12.2.2, Git 2.56.1, Docker 29.9.3 and Compose 5.7.1 detected.
- Python 3.12 unavailable; Docker daemon access denied. No containers were built or started.

Phase 2 adds separate ingestion setup and validation in [the ingestion guide](ingestion.md).

Phase 3 discovered an existing managed Python 3.12.15 outside PATH; the initial preflight
reported only command availability. Core tests now pass on that interpreter as well as
Python 3.14.10. Activate or select the managed interpreter before running the preflight.
Actual ML execution used the cached Python 3.14 wheels in `.venv`; retrain in the deployment
runtime rather than copying those artifacts to a Python 3.12 service. See [training](training.md).

Phase 5 provides [inference setup](inference.md) and socket-free ASGI tests. The sandbox
denies socket-pair writes used by threaded async test clients; these tests inject a
synchronous executor while running real API validation and real model scoring. A normal
host still needs to validate the listening HTTP/threadpool and Docker paths.

# flowsage-backend

FastAPI backend for FlowSage. This first slice is intentionally just the load-bearing
plumbing — app factory, settings, async Postgres engine, Alembic migrations, and a
`/healthz` endpoint that actually round-trips a query — so later endpoints (auth,
simulations, event ingestion) land on infrastructure that's already verified working.

## Setup

```bash
cd backend
uv sync
docker compose -f ../infra/docker-compose.yml up -d postgres
cp ../.env.example .env   # then edit DATABASE_URL if needed
```

## Run

```bash
uv run flowsage-backend           # http://localhost:8000, see /healthz
# or, with autoreload for development:
uv run uvicorn flowsage_backend.main:create_app --factory --reload
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "add users table"
uv run alembic upgrade head
```

`migrations/env.py` gets its DB URL from `Settings` (env vars / `.env`), not from
`alembic.ini` — so migrations always target the same database the app itself would.

## Auth

Single-tenant, per the plan's Phase 1 scope: one seeded user, no public signup.
Login sets a JWT in an httpOnly cookie; there's no bearer-token flow.

```bash
# Seed (or reset the password of) the one user account
uv run flowsage-backend create-user admin@example.com supersecret123

# Then, against a running server:
curl -c cookies.txt -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "supersecret123"}'
curl -b cookies.txt localhost:8000/auth/me
curl -b cookies.txt -X POST localhost:8000/auth/logout
```

Passwords are hashed with Argon2id (`argon2-cffi`). `JWT_SECRET` must be overridden
via env var outside local dev (see `.env.example`) — the default is a clearly-marked
placeholder, and `Settings` refuses to start with it set if `ENVIRONMENT` isn't
`development`.

## Simulations

`POST /simulations` uploads a screenshot sequence and a persona, then enqueues a
`run_simulation_job` on an arq/Redis queue instead of running the walkthrough inline
(Claude vision calls per screen are too slow for one request/response cycle). A
separate worker process picks the job up, walks the screenshots with
`flowsage_predict.agent.iter_persona_walkthrough`, and persists each step/friction
issue as it happens.

```bash
uv sync --all-extras   # picks up flowsage-predict as a workspace dependency
uv run flowsage-backend seed-personas   # loads the 5 baseline personas
uv run flowsage-worker                  # separate process/terminal
uv run flowsage-backend                 # the API itself

curl -b cookies.txt -X POST localhost:8000/simulations \
  -F "persona_id=<uuid from GET /personas>" \
  -F "goal=Complete purchase" \
  -F "flow_name=Checkout Flow" \
  -F "files=@../scripts/sample_data/screenshots/01_cart.png" \
  -F "files=@../scripts/sample_data/screenshots/02_shipping.png"

curl -b cookies.txt localhost:8000/simulations/<run-id>          # poll
curl -b cookies.txt localhost:8000/simulations/<run-id>/stream   # SSE, live
```

`GET /simulations/{id}/stream` polls the DB (not Redis pub/sub) until the run
completes or fails — simpler, and this single-tenant Phase 1 already has Postgres.
Uploaded filenames are sanitized to their basename before touching disk, so a
crafted `../../etc/evil.png` can't escape `UPLOAD_DIR`.

### Full stack via docker-compose

```bash
docker compose -f ../infra/docker-compose.yml up -d postgres redis backend worker
docker compose -f ../infra/docker-compose.yml exec backend \
  python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f ../infra/docker-compose.yml exec backend \
  flowsage-backend seed-personas
```

`backend` and `worker` share the same image and an `uploads_data` volume so both
can see the uploaded screenshots. The image pre-creates and `chown`s `/data/uploads`
before switching to the non-root `appuser` -- otherwise a fresh named volume mounts
in root-owned and the upload endpoint gets a `PermissionError` (a real bug caught
by testing this exact docker-compose path end to end, not just `docker build`).

## Development

```bash
uv sync --all-extras
uv run autoflake8 --recursive --in-place src tests migrations
uv run black src tests migrations
uv run mypy --strict src
uv run pytest
```

Tests spin up their own ephemeral Postgres via [testcontainers](https://testcontainers-python.readthedocs.io/)
(session-scoped fixture in `tests/conftest.py`), so `pytest` never depends on
`infra/docker-compose.yml` already being up, and never touches a real database.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` (pydantic-settings, env vars / `.env`) |
| `db.py` | Async SQLAlchemy engine + session factory construction |
| `models/` | SQLAlchemy ORM models (declarative `Base` lives in `models/base.py`) |
| `security.py` | Argon2id password hashing, JWT access-token encode/decode |
| `seed.py` | Create/reset the single-tenant user, load the 5 baseline personas |
| `deps.py` | FastAPI dependencies: DB session, `get_current_user` (reads the session cookie) |
| `simulations.py` | `create_run`/`execute_simulation` -- the testable core of the simulation lifecycle |
| `worker.py` | arq `WorkerSettings` + `run_simulation_job`, run via `flowsage-worker` |
| `api/auth.py` | `/auth/login`, `/auth/logout`, `/auth/me` |
| `api/personas.py` | `GET /personas` |
| `api/simulations.py` | `POST /simulations`, `GET /simulations/{id}`, `GET /simulations/{id}/stream` (SSE) |
| `main.py` | `create_app()` FastAPI factory, lifespan-managed engine + arq pool, `/healthz` |
| `__main__.py` | `flowsage-backend` console script: `serve` (default), `create-user`, `seed-personas` |
| `migrations/` | Alembic environment, wired to `Settings.database_url` and `models.Base.metadata` |

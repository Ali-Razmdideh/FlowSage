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
| `main.py` | `create_app()` FastAPI factory, lifespan-managed engine, `/healthz` |
| `__main__.py` | `flowsage-backend` console script (runs uvicorn) |
| `migrations/` | Alembic environment, wired to `Settings.database_url` and `models.Base.metadata` |

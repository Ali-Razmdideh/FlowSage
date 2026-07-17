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
placeholder, and PyJWT will warn if it's ever set below 32 bytes.

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
| `seed.py` | Create/reset the single-tenant user (`upsert_user`) |
| `deps.py` | FastAPI dependencies: DB session, `get_current_user` (reads the session cookie) |
| `api/auth.py` | `/auth/login`, `/auth/logout`, `/auth/me` |
| `main.py` | `create_app()` FastAPI factory, lifespan-managed engine, `/healthz` |
| `__main__.py` | `flowsage-backend` console script: `serve` (default) or `create-user <email> <password>` |
| `migrations/` | Alembic environment, wired to `Settings.database_url` and `models.Base.metadata` |

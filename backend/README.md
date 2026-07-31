# flowsage-backend

FastAPI backend for FlowSage: multi-tenant workspaces, the predictive engine's simulations API, the observational engine's event ingestion + journey graph, calibration, billing, and integrations.

## Setup

```bash
cd backend
uv sync --all-extras   # picks up flowsage-predict/flowsage-graph as workspace dependencies
docker compose -f ../infra/docker-compose.yml up -d postgres redis neo4j
cp ../.env.example .env   # then edit DATABASE_URL etc. if needed
```

## Run

```bash
uv run flowsage-backend            # http://localhost:8000, see /healthz
uv run flowsage-worker             # separate process — arq job queue (simulations, retraining, digests)
# or, with autoreload for development:
uv run uvicorn flowsage_backend.main:create_app --factory --reload
```

```bash
uv run flowsage-backend seed-personas             # loads the 5 baseline personas into a workspace
uv run flowsage-backend create-user <email> <pw>  # create (or reset the password of) a user
uv run flowsage-backend create-api-key <workspace-slug> <name>  # mint a workspace API key
```

## Auth

Two auth mechanisms, both resolved by a single dependency (`deps.get_current_actor`) on the routes that need to serve both a browser and a non-browser client (the Figma plugin, the Insights API):

- **Session cookie** — login sets a JWT in an httpOnly cookie (`POST /auth/login`), scoped to the active workspace. Used by the web app.
- **`X-API-Key` header** — a per-workspace key minted at `/settings/integrations` (or the `create-api-key` CLI command). Used by `POST /v1/events`, `/v1/insights/*`, the Figma plugin, and any external ingestion.

Passwords are hashed with Argon2id. `JWT_SECRET`/`SECRET_ENCRYPTION_KEY` must be overridden via env var outside local dev — `Settings` refuses to start with the placeholder values if `ENVIRONMENT != development`. CORS is enabled with `allow_credentials=False` so cookie-authenticated routes stay same-origin-only while API-key routes remain reachable from a null-origin client like the Figma plugin's UI iframe.

## Route map

| Prefix | Auth | What |
|---|---|---|
| `/auth` | cookie | login/logout/me, `switch-workspace` |
| `/workspaces` | cookie | list/create workspaces, current workspace settings + archive, member invite/role/remove |
| `/personas` | cookie (`GET` also accepts API key) | persona library CRUD, baseline reset |
| `/simulations` | cookie (also accepts API key) | upload a screenshot sequence, poll status, SSE stream |
| `/v1/events` | API key | event ingestion (SDK/webhook/Figma plugin) |
| `/graph` | cookie | funnel, cohort comparison, churn-risk segments, per-node intelligence, Slack/Jira export |
| `/calibration` | cookie | predicted-vs-observed report, trigger/poll a retraining job |
| `/settings/model-calibration` | cookie | anomaly threshold, churn-alert threshold, auto-retrain toggle, digest frequency |
| `/alerts` | cookie | current alerts, manually trigger the digest |
| `/friction-issues` | cookie | export a specific issue to Slack/Jira |
| `/settings/integrations` | cookie | Slack/Jira connection, API keys, webhooks + delivery log |
| `/onboarding` | cookie | checklist status, import bundled sample data |
| `/audit-logs` | cookie | keyset-paginated security/audit log |
| `/billing` | cookie (webhook is unauthenticated + signature-verified) | usage snapshot, Stripe Checkout/Portal, webhook |
| `/v1/insights` | API key | public funnel + friction-issue read API for downstream tooling |

Full request/response schemas: `/api/docs` (Swagger UI) once the server is running, or the narrative version at the frontend's `/docs` page.

## Simulations (predictive engine)

`POST /simulations` uploads a screenshot sequence and a persona, then enqueues a `run_simulation_job` on the arq/Redis queue instead of running the walkthrough inline (Claude vision calls per screen are too slow for one request/response cycle). The worker walks the screenshots with `flowsage_predict.agent.iter_persona_walkthrough`, persisting each step/friction issue as it happens. Screens are ordered by filename (sorted lexicographically) and the friction report's `screen` field is the filename's stem — the Figma plugin relies on this being a plain zero-padded index (`001`, `002`, …).

## Events & journey graph (observational engine)

`POST /v1/events` stores events in Postgres (the source of truth `GET /graph/funnel` queries) and best-effort mirrors them into Neo4j as a temporal graph via `flowsage_graph.ingest.Neo4jGraphSink` — the same library the `flowsage-graph` CLI uses. If Neo4j is unreachable, Postgres ingestion still succeeds; the graph mirror is just skipped with a logged warning. `GET /graph/funnel` re-derives the funnel/friction breakdown from the raw event log on every call, reusing `flowsage_graph.funnel.discover_funnel`/`detect_friction` unchanged, so the same rage-loop/backtrack detection runs identically whether you use `flowsage-graph` standalone or through this API (Neo4j's own schema only keeps aggregated per-session transition edges, which loses the same-screen repeat visits that rage-loop detection needs).

## Calibration, churn, alerts

`calibration.py` matches each persona's latest completed simulation's predicted friction against the corresponding screen's observed drop-off (matched by screen name, computed on demand — no stored calibration table, so nothing goes stale). `churn.py` scores cohorts by drop-off + friction density. `retraining.py` nudges a miscalibrated persona's behavioral sliders and records a `PersonaMemory` entry with the evidence. `alerts.py` builds the weekly digest and evaluates fixed-threshold calibration/churn alerts; `worker.py`'s cron job sends it via Slack/webhook only when due, per the workspace's configured digest frequency.

## Billing

`billing.py` computes a workspace's tier (Free/Pro/Team) and per-resource usage (events/runs/seats) on demand and enforces it (`check_within_limits`, called before event ingestion, simulation creation, and member invites — a 402, not a soft warning). Stripe Checkout/Portal/webhook client lives in `integrations/stripe_client.py`. All Stripe env vars are optional — unset, `/billing/usage` still works (every workspace reads as Free tier) while checkout/portal/webhook return a clean 400.

## Development

```bash
uv sync --all-extras
uv run autoflake8 --recursive --in-place src tests migrations
uv run black src tests migrations
uv run mypy --strict src
uv run pytest
```

Tests spin up their own ephemeral Postgres via [testcontainers](https://testcontainers-python.readthedocs.io/) (session-scoped fixture in `tests/conftest.py`), so `pytest` never depends on `infra/docker-compose.yml` already being up, and never touches a real database. `migrations/env.py` gets its DB URL from `Settings` (env vars / `.env`), not `alembic.ini`, so migrations always target the same database the app itself would:

```bash
uv run alembic revision --autogenerate -m "add a thing"
uv run alembic upgrade head
```

## Module map

| Area | Modules |
|---|---|
| App wiring | `main.py` (factory, lifespan-managed engine/arq pool/Neo4j sink), `config.py` (`Settings`), `db.py`, `deps.py` (auth dependencies), `rate_limit.py`, `worker.py` (arq jobs + cron), `__main__.py` (CLI) |
| Auth & workspaces | `security.py`, `seed.py`, `api/auth.py`, `api/workspaces.py`, `models/user.py`, `models/workspace.py` |
| Predictive engine | `simulations.py`, `api/simulations.py`, `api/personas.py`, `models/simulation.py`, `models/persona.py` |
| Observational engine | `events.py`, `api/events.py`, `models/event.py` |
| Calibration & churn | `calibration.py`, `churn.py`, `retraining.py`, `insights.py`, `models/calibration.py` |
| Alerts & exports | `alerts.py`, `api/alerts.py`, `api/exports.py` |
| Integrations | `integrations/` (Slack, Jira, webhooks, Stripe client), `integrations_store.py`, `webhooks_store.py`, `api/integrations.py`, `models/integration.py`, `models/webhook.py`, `models/api_key.py` |
| Billing | `billing.py`, `billing_store.py`, `api/billing.py`, `models/billing.py` |
| Security & settings | `audit.py`, `crypto.py`, `url_safety.py`, `api/audit.py`, `api/settings.py`, `settings_store.py`, `models/audit_log.py`, `models/settings.py` |
| Onboarding | `onboarding.py`, `api/onboarding.py`, `resources/` (bundled sample data) |
| Migrations | `migrations/` — Alembic environment, wired to `Settings.database_url` and `models.Base.metadata` |

# Phase 4 chunk 1 — Public Insights API + hardening (load test, API test coverage)

**Status:** Approved 2026-07-25. Covers `plans/full-project-coding-plan.md` Phase 4 items 2 ("Public Insights API (`/v1/insights/...`) documented via OpenAPI") and 4 ("Hardening: e2e suite, load test ingestion" — scoped to what's new here, since SOC2 hardening — audit log, rate limiting, secrets encryption — already shipped in Phase 3 chunk 3).

## Context

Every prior write path into FlowSage (`POST /v1/events`) already has API-key auth (`require_workspace_api_key` in `deps.py`, backing an `ApiKey` row hashed at rest). There is no public *read* path yet — everything else (`/graph/funnel`, `/graph/churn-risk`, etc.) requires a browser session cookie via `get_current_membership`, which an external integration can't use. Phase 4's plan bullet calls for a documented public Insights API using the same API-key model, read-only.

Phase 3 chunk 3 already shipped the SOC2-track hardening (audit log, Redis rate limiting, Fernet secrets encryption, retention purge). What's left of the plan's Phase 4 "hardening" bullet, scoped to this chunk, is test coverage and load testing for the new surface being added here — not re-doing Phase 3.

## Scope

**In scope:**
- `GET /v1/insights/funnel` — same query contract as the existing `/graph/funnel` (cohort/device/since filters), API-key auth instead of cookie auth, reusing `build_funnel_report` unchanged.
- `GET /v1/insights/friction-issues` — new keyset-paginated listing of `FrictionIssue` rows for the caller's workspace (filters: severity, screen, since), following `audit.py`'s existing cursor pattern.
- OpenAPI documentation: `insights` tag description, `FastAPI(...)` app gets a `description`/`version`, and a docs-only `APIKeyHeader` security scheme on the insights router so Swagger UI's Authorize button and per-endpoint lock icon work (existing `require_workspace_api_key` logic is untouched — this is additive, for documentation only).
- Backend pytest integration tests for both endpoints (workspace isolation, pagination, filters, auth failure).
- `scripts/load_test/ingest_load_test.py`: a standalone asyncio+httpx script that load-tests `POST /v1/events`, run manually against a live stack.

**Out of scope (explicitly deferred):**
- Churn-risk / cohort-compare under `/v1/insights/` — nothing currently needs them externally; can be added later by copying this chunk's pattern if requested.
- A Playwright e2e spec — this is a pure API surface with no frontend screen behind it (same reasoning as Phase 2 chunk 4's settings scope cuts: don't build UI/browser tests for something with no UI). Verified instead by curl against the live `docker-compose` stack at closeout, matching every prior phase's actual verification pattern.
- Any changes to `POST /v1/events`'s auth or the existing `/graph/*` cookie-authed endpoints — untouched.
- Wiring the load-test script into CI — load tests are a manual/ad-hoc tool, not a CI gate.
- Rate limiting on the new insights endpoints beyond the existing default (300/minute per-key via the same `Limiter` — `require_workspace_api_key` already resolves an identity the limiter can key on, no new work needed).

## Architecture & Data Flow

**Compute module** (`backend/src/flowsage_backend/insights.py`, mirrors `calibration.py`/`churn.py`'s compute-on-demand pattern — no new tables):
- `list_friction_issues(session, workspace_id, *, severity=None, screen=None, since=None, cursor=None, limit=50) -> tuple[list[FrictionIssue], str | None]` — keyset pagination on `(created_at desc, id desc)`, same shape as `audit.py`'s `list_audit_logs`. Cursor encode/decode helpers are duplicated locally (not extracted into a shared util), consistent with how `audit.py`'s cursor helpers are already private/module-local rather than generic.
- Funnel data has no new function — `GET /v1/insights/funnel` calls the existing `flowsage_backend.events.build_funnel_report` directly, same as `/graph/funnel` does.

**Router** (`backend/src/flowsage_backend/api/insights.py`):
```python
insights_router = APIRouter(
    prefix="/v1/insights",
    tags=["insights"],
    dependencies=[Depends(require_workspace_api_key), Security(api_key_header_scheme)],
)
```
`api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)` is added purely so FastAPI's OpenAPI generator documents the header and Swagger UI shows an Authorize control; the actual auth decision stays in `require_workspace_api_key` (which independently reads `request.headers["X-API-Key"]` and validates the hash) — the two deps run side by side, no behavior change to auth, just to docs.

**OpenAPI metadata**: `main.py`'s `FastAPI(title="FlowSage API", ...)` gains `description="..."` and `version="0.4.0"` (bumped from Phase 3's implicit default), plus `openapi_tags=[{"name": "insights", "description": "Public, API-key-authenticated read endpoints for external integrations."}]`.

**Load test script** (`scripts/load_test/ingest_load_test.py`): argparse CLI — `--url` (default `http://localhost:8000`), `--api-key` (required), `--concurrency` (default 10), `--total` (default 1000), `--batch-size` (events per POST, default 1, matching real client behavior of small batches). Fires `POST /v1/events` requests concurrently via `asyncio.gather` bounded by a `asyncio.Semaphore(concurrency)`, records per-request latency, prints p50/p95/p99, throughput (req/s), and error rate at the end. Not a package (no `pyproject.toml` member, no CI wiring) — a single script under `scripts/load_test/`, run with `uv run --with httpx python scripts/load_test/ingest_load_test.py ...` or from the backend's venv since `httpx` is already a main dependency there.

## Components

| File | Purpose |
|---|---|
| `backend/src/flowsage_backend/insights.py` | `list_friction_issues()` + cursor helpers |
| `backend/src/flowsage_backend/api/insights.py` | `GET /v1/insights/funnel`, `GET /v1/insights/friction-issues` |
| `backend/src/flowsage_backend/main.py` | register `insights_router`, add OpenAPI `description`/`version`/`openapi_tags` |
| `backend/tests/test_insights.py` | integration tests: auth, isolation, pagination, filters |
| `scripts/load_test/ingest_load_test.py` | manual load-test tool for `POST /v1/events` |
| `scripts/load_test/README.md` | how to run it, how to read the output |

## Error Handling

- Missing/invalid `X-API-Key`: existing `require_workspace_api_key` behavior (401), unchanged.
- Invalid `cursor` on `friction-issues`: matches `audit.py`'s existing (unfixed) behavior exactly — a malformed cursor raises an uncaught `ValueError` in `_decode_cursor`, surfacing as a 500. Not introducing a fix here `audit.py` itself doesn't have, to avoid two paginated endpoints behaving inconsistently; a real client never hand-constructs a cursor, only echoes back what `next_cursor` gave it.
- No friction issues / empty funnel: normal empty-list/empty-report response, not an error (matches every other list endpoint in this codebase).

## Testing

- `test_insights.py`: funnel endpoint reachable with a valid key, filters work, wrong-workspace key can't see another workspace's funnel; friction-issues pagination (page through >1 page, cursor stability), severity/screen/since filters, 401 on missing/bad key, 401 on a revoked key.
- Full backend suite + mypy --strict + autoflake8 must stay green (196 existing tests + new ones).
- Closeout verification: full `docker-compose up -d --build`, create a real API key via `/settings/integrations`, curl both new endpoints, run the load-test script against the live `backend` container and record the results in the commit/memory.

## Load Test — what "done" looks like

Not a pass/fail gate with a hardcoded threshold (no prior baseline exists to compare against, and this is a single-container dev-compose stack, not production sizing) — the deliverable is the *tool* plus one recorded baseline run's numbers, so future changes have something to compare against. Script output should make p95 latency and error rate obvious at a glance.

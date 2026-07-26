# Load test: `POST /v1/events`

Manual tool, not part of CI or the pytest suite. Hits a live, already-running
backend the same way `frontend/e2e/README.md`'s tests do.

## Setup

1. Bring up the stack: `docker compose -f infra/docker-compose.yml up -d --build`
2. Migrate + create a user + create an API key via `/settings/integrations` in
   the running frontend (or `flowsage-backend create-user` + the API for a key).
3. Run:

```bash
uv run --project backend python scripts/load_test/ingest_load_test.py \
  --url http://localhost:8000 --api-key <your-key> --concurrency 5 --total 100
```

`POST /v1/events` is rate-limited to 120/minute per API key (`INGEST_RATE_LIMIT`
in `backend/src/flowsage_backend/rate_limit.py`). A burst well above that in a
single run (e.g. `--total 2000` at high concurrency, finishing in ~10s) will
mostly measure the rate limiter rejecting requests with `429`, not real
ingestion latency — that's correct, working-as-designed behavior, not a bug,
but it isn't a useful throughput number. Keep `--total`/`--concurrency` sized
so the run stays under ~120 requests/minute if you want to measure the
ingestion path itself rather than the rate limiter.

## Reading the output

- `throughput`: requests/second sustained across the whole run.
- `latency p50/p95/p99`: per-request wall-clock time including the semaphore wait.
- `error rate`: fraction of requests that didn't return `201`.

There's no pass/fail threshold baked in — this is a single-container dev-compose
stack, not a production sizing target. The point is having a repeatable number to
compare against after future changes to the ingestion path, not a gate.

## Baseline run (recorded 2026-07-25)

Two runs against a local `docker compose up -d --build` stack (single-container
dev sizing, not a production target):

**Burst above the rate limit** (`--concurrency 20 --total 2000`, ~9s wall time —
demonstrates the rate limiter, not raw ingestion throughput):
```
requests: 2000  errors: 1881  wall: 9.33s
throughput: 214.3 req/s
latency p50: 5149.6ms  p95: 8860.9ms  p99: 9180.4ms  mean: 5087.2ms
error rate: 94.0%
```
1881/2000 requests were rejected with `429` by the per-key `INGEST_RATE_LIMIT`
(120/minute) — confirmed via a direct curl returning `429` mid-run. This is the
rate limiter working correctly under burst load, not an ingestion-path bug.

**Within the rate limit** (`--concurrency 5 --total 100`, real ingestion-path
measurement):
```
requests: 100  errors: 0  wall: 0.56s
throughput: 179.2 req/s
latency p50: 279.5ms  p95: 510.5ms  p99: 518.0ms  mean: 277.1ms
error rate: 0.0%
```

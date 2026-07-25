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
  --url http://localhost:8000 --api-key <your-key> --concurrency 20 --total 2000
```

## Reading the output

- `throughput`: requests/second sustained across the whole run.
- `latency p50/p95/p99`: per-request wall-clock time including the semaphore wait.
- `error rate`: fraction of requests that didn't return `201`.

There's no pass/fail threshold baked in — this is a single-container dev-compose
stack, not a production sizing target. The point is having a repeatable number to
compare against after future changes to the ingestion path, not a gate.

## Baseline run (recorded 2026-07-25)

Record actual numbers here after running Task 4's closeout verification pass.

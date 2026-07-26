# CI Hardening Design Spec

**Phase:** 4, item 4 (hardening) — first of 3 sub-chunks (CI hardening → deploy → landing/docs, each its own spec/plan/worktree cycle).

## Problem

`.github/workflows/ci.yml` only runs a Python matrix job (`autoflake8` → `black` → `mypy --strict` → `pytest` across `scripts/flowsage-predict`, `scripts/flowsage-graph`, `backend`). It has never run any frontend check (oxlint, `tsc`, vitest, production build) or the 5 existing Playwright e2e specs (`frontend/e2e/*.spec.ts`). Every frontend/e2e verification to date has been manual, done by hand at the end of each phase. This chunk closes that gap by wiring both into CI — no new tests are being written; the specs and checks already exist and pass locally.

## Design

Two new jobs added to `.github/workflows/ci.yml`, alongside (not replacing) the existing `python` job. Both trigger on the same `push`/`pull_request` events already configured (`branches: [main]`), running in parallel with `python` and with each other — no `needs:` gating, since the jobs are independent and gating would only add latency for no benefit.

### Job 1: `frontend`

Fast (~1 minute), no Docker. Working directory `frontend/`.

1. `actions/setup-node@v4`, `node-version: "22"`, `cache: npm`, `cache-dependency-path: frontend/package-lock.json`
2. `npm ci`
3. `npm run lint` (oxlint)
4. `npm run typecheck` (`tsc -b`)
5. `npm test` (`vitest run`)
6. `npm run build` (`tsc -b && vite build`)

Steps run in this order (cheapest/fastest-failing first) so a lint break fails in seconds, not after a 30s vitest run.

### Job 2: `e2e`

Slow (few minutes), full docker-compose stack — mirrors exactly what `frontend/e2e/README.md` already documents as the manual setup, just automated.

1. `docker compose -f infra/docker-compose.yml build`
2. `docker compose -f infra/docker-compose.yml up -d` (all 6 services: postgres, redis, neo4j, backend, worker, frontend)
3. Wait for `backend` to report healthy (poll, same pattern as the compose file's own healthchecks)
4. `docker compose exec backend python -m alembic -c /workspace/backend/alembic.ini upgrade head`
5. `docker compose exec backend flowsage-backend seed-personas`
6. `docker compose exec backend flowsage-backend create-user e2e@flowsage.dev supersecret123`
7. `npx playwright install --with-deps chromium` (the only browser `playwright.config.ts` needs — no `projects` array means the implicit single default project, which is Chromium)
8. `npx playwright test` from the GitHub Actions runner, hitting `http://localhost:5173` — the `frontend` service's published port, i.e. the real production nginx build (`frontend/Dockerfile`), not `vite dev`. `playwright.config.ts`'s existing `baseURL: "http://localhost:5173"` needs no change.
9. `docker compose -f infra/docker-compose.yml down -v`, as a step with `if: always()`, so a failed run doesn't leak containers/volumes into the next CI run on the same runner.

No `ANTHROPIC_API_KEY` secret is needed: per `frontend/e2e/README.md`, the Predictive Engine e2e assertion only checks that a simulation run reaches a terminal state (completed or failed), not that it succeeds — this project's existing documented behavior, unchanged here.

### What does not change

- `playwright.config.ts`, `infra/docker-compose.yml`, and all 5 existing e2e spec files are already correct for this — this chunk is CI wiring only, no test-content changes.
- The existing `python` job is untouched.

### Follow-up doc update

`frontend/e2e/README.md` currently only documents manual local setup. Add a short note that CI now runs this suite automatically on every push/PR (via the `e2e` job above), so a contributor reading it understands the manual steps are for local iteration, not the only way these tests run.

## Verification

- Push a throwaway commit to a branch and confirm both new jobs go green in GitHub Actions before merging this chunk's branch to `main`.
- Confirm the `e2e` job's teardown step actually runs (check Actions log) even when a deliberately-broken test is pushed first, to validate the `if: always()` teardown before reverting the deliberate break.

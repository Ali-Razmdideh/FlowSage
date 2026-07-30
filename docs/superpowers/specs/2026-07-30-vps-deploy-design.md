# VPS Deploy Design Spec

**Phase:** 4, item 4 (hardening) — second of 3 sub-chunks (CI hardening → **deploy** → landing/docs, each its own spec/plan/worktree cycle).

## Problem

FlowSage only runs via local `docker compose up` (`infra/docker-compose.yml`). There is no production deploy target. The user has a VPS and a domain already provisioned; this chunk gets the existing compose stack running there behind TLS, with secrets handled safely and a repeatable deploy/backup process — no code changes to the application itself.

## Design

### Topology

```
Internet ──443/80──▶ Caddy (compose service, host-published) ──▶ frontend:80 (nginx, internal Docker network)
                                                                        │ /api/* proxy_pass (frontend/nginx.conf, unchanged)
                                                                        ▼
                                                                   backend:8000 (internal only)
                              postgres / redis / neo4j / worker: internal-network only, no host port publish
```

`frontend/nginx.conf` already proxies `/api/` to `backend:8000` inside the compose network, so Caddy only ever points at one upstream (`frontend`). Stripe's webhook URL in production is `https://<domain>/api/billing/webhook`.

### New files (all under `infra/`, versioned)

- **`infra/docker-compose.prod.yml`** — override merged via `-f docker-compose.yml -f docker-compose.prod.yml`:
  - Adds a `caddy` service (official `caddy:2-alpine` image), publishing host `80`/`443`, mounting `infra/Caddyfile` and named volumes for its certificate/config state.
  - Removes the host `ports:` mapping on `postgres`, `redis`, `neo4j`, `backend`, `worker` (base compose file's port publishing is dev-only; override replaces those service definitions' `ports:` with an empty list — internal Docker network DNS is all these services need to reach each other, both in dev and here).
  - Republishes `frontend` to `127.0.0.1:8080:80` (loopback-only, for on-box debugging — Caddy itself reaches `frontend` over the internal Docker network at port 80, not via this loopback mapping).

- **`infra/Caddyfile`**:
  ```
  {$DOMAIN} {
      reverse_proxy frontend:80
  }
  ```
  Caddy resolves `frontend` via the compose network's internal DNS, matching every other inter-service reference in this project (`bolt://neo4j:7687`, `redis://redis:6379/0`, etc.). Automatic Let's Encrypt cert issuance/renewal, no extra config needed.

- **`infra/.env.prod.example`** — template documenting every required override for a real deploy: `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `APP_BASE_URL=https://${DOMAIN}`, `DOMAIN=<placeholder>`, `JWT_SECRET`/`SECRET_ENCRYPTION_KEY` (generate via `openssl rand -hex 32`, comment noting the existing `_reject_placeholder_secret_outside_dev` startup validator in `backend/src/flowsage_backend/config.py` hard-fails boot if these are left as dev placeholders once `ENVIRONMENT=production`), real `POSTGRES_*`/`NEO4J_*` credentials, and the `STRIPE_*` keys (left blank is fine — billing endpoints already degrade to clean 400s unconfigured, per the existing Stripe design). The real `.env.prod` lives only on the server, is `chmod 600`, and is added to `.gitignore` (pattern `infra/.env.prod` — the `.example` file stays tracked).

- **`infra/deploy.sh`** — run by hand over SSH on the VPS from the repo checkout:
  1. `git pull`
  2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d --build`
  3. Poll `docker compose ps --format json` until `postgres`/`redis`/`neo4j`/`backend` all report healthy (reuses the healthchecks already defined in the base compose file), with a timeout that exits non-zero and prints the last 50 lines of backend logs if it's never reached.
  4. `docker compose exec -T backend /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head`
  5. Print a one-line success message with the image tag/commit SHA deployed.
  Idempotent — safe to re-run if any step fails partway.

- **`infra/backup-postgres.sh`** — `docker compose exec -T postgres pg_dump -U $POSTGRES_USER $POSTGRES_DB | gzip > /var/backups/flowsage/postgres-$(date +%F).sql.gz`, then deletes any file in that directory older than 14 days. Meant to be invoked from a host crontab (documented, not installed automatically — see `DEPLOY.md`), e.g. nightly at 03:00.

- **`infra/DEPLOY.md`** — runbook covering: first-time server setup (install Docker Engine + Compose plugin, clone the repo, copy `.env.prod.example` → `.env.prod` and fill in real values, set the crontab entry for `backup-postgres.sh`), running `deploy.sh` for both first deploy and subsequent updates, the restore drill (`gunzip -c <backup> | docker compose exec -T postgres psql -U $POSTGRES_USER $POSTGRES_DB`), and the end-to-end curl check below.

### Error handling / safety

- No new application-level error handling — the existing `_reject_placeholder_secret_outside_dev` validator (`backend/src/flowsage_backend/config.py`) is the safety net that already hard-fails startup if `ENVIRONMENT=production` and secrets are still dev placeholders. This chunk relies on it rather than re-implementing a check.
- `deploy.sh` runs the Alembic migration only after confirming containers are healthy, so a migration never races a not-yet-ready Postgres.
- Brief downtime during `docker compose up -d --build` (containers recreated in place) is accepted for this manual-deploy MVP — no blue/green, no rolling restart, explicitly out of scope.

### Out of scope this pass

- Real domain name (placeholder `${DOMAIN}` swapped in by the user at actual deploy time).
- GitHub Actions auto-deploy (manual SSH deploy only, per earlier decision).
- Neo4j backups (Postgres only, per earlier decision).
- Blue/green or zero-downtime deploys.

## Verification

- From the VPS, after `deploy.sh` completes: `curl -i https://<domain>/api/billing/usage` → `401` (confirms TLS termination, Caddy → frontend → nginx `/api/` proxy → backend chain all work end-to-end, and auth is enforced).
- `curl -i https://<domain>/` → `200` with the SPA's `index.html` (confirms the non-API path serves the frontend build).
- Run `backup-postgres.sh` once manually, confirm a `.sql.gz` file lands in `/var/backups/flowsage/`, then perform the restore drill against a scratch database to prove the dump is actually restorable (not just "a file exists").
- Confirm `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` (no `up`) resolves cleanly with `.env.prod` sourced — catches override-file typos before they hit the real server.

# VPS Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the existing `infra/docker-compose.yml` stack deployable to a VPS behind Caddy/TLS, with safe secrets handling, a repeatable deploy script, and Postgres backups — per `docs/superpowers/specs/2026-07-30-vps-deploy-design.md`.

**Architecture:** A `infra/docker-compose.prod.yml` override merges with the existing base compose file (`-f docker-compose.yml -f docker-compose.prod.yml`), adding a `caddy` reverse-proxy service and clearing host port publishing on every internal service except `frontend` (loopback-only) and `caddy` (public 80/443). `frontend/nginx.conf` already proxies `/api/` to `backend:8000` internally, so Caddy only ever points at `frontend:80`. A `deploy.sh` script and a `backup-postgres.sh` cron script are the only two operational entry points; a `DEPLOY.md` runbook documents first-time setup.

**Tech Stack:** Docker Compose (v2, Compose Spec — no `version:` key), Caddy 2, bash, existing FastAPI/Alembic backend image.

## Global Constraints

- No application code changes — infra/ops only, per the spec's Problem statement.
- Real `infra/.env.prod` is never committed (gitignored, `chmod 600` on the server); `infra/.env.prod.example` is the tracked template.
- Backups cover Postgres only, 14-day retention, nightly cron (documented, not auto-installed).
- Manual SSH deploy only — no CI auto-deploy in this pass.
- Domain is a placeholder (`${DOMAIN}` env var) — no real domain wired in this session.
- Every new shell script starts with `#!/usr/bin/env bash` + `set -euo pipefail`, matching the rigor (if not the exact language) of this project's existing `scripts/` tooling.

---

### Task 1: `infra/docker-compose.prod.yml` + `infra/Caddyfile`

**Files:**
- Create: `infra/docker-compose.prod.yml`
- Create: `infra/Caddyfile`

**Interfaces:**
- Consumes: `infra/docker-compose.yml`'s existing service names (`postgres`, `redis`, `neo4j`, `backend`, `worker`, `frontend`) — must match exactly for the override to apply to the right services.
- Produces: a `caddy` service other tasks' verification steps and `DEPLOY.md` (Task 5) reference by name.

- [ ] **Step 1: Write `infra/Caddyfile`**

```
{$DOMAIN} {
	reverse_proxy frontend:80
}
```

- [ ] **Step 2: Write `infra/docker-compose.prod.yml`**

```yaml
# Merge with the base file for a production deploy:
#   docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build
#
# Clears host port publishing on every internally-facing service (base file's
# ports are dev-convenience only) and adds Caddy as the sole public entrypoint,
# terminating TLS and reverse-proxying to `frontend`, which already proxies
# `/api/` to `backend:8000` internally (frontend/nginx.conf) -- so Caddy never
# needs to know about the backend service directly.
services:
  postgres:
    ports: !reset []

  redis:
    ports: !reset []

  neo4j:
    ports: !reset []

  backend:
    ports: !reset []

  frontend:
    # Loopback-only, for on-box debugging -- Caddy reaches `frontend` over the
    # internal Docker network at port 80, not via this mapping.
    ports: !reset
      - "127.0.0.1:8080:80"

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    environment:
      DOMAIN: "${DOMAIN}"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frontend

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 3: Verify the override resolves and ports are actually cleared**

Run:
```bash
cd /home/asus/Projects/personal/FlowSage
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml config
```
Expected: no errors. In the printed resolved config, confirm:
- `postgres`, `redis`, `neo4j`, `backend` each show no `ports:` key (or an empty one)
- `frontend` shows only `published: "8080"`, `target: 80`, with a `host_ip` of `127.0.0.1`
- `caddy` shows `published: "80"`/`"443"` with no `host_ip` restriction

If `!reset` isn't honored by the installed Compose version (check `docker compose version` — needs Compose v2.24+), the resolved config will show the *merged* (base + override) port list instead of the override-only list for that service; in that case stop and re-investigate rather than proceeding with an insecure deploy — this project's Compose version was already confirmed to support it (`docker compose version` → `Docker Compose version 5.3.1`), so this branch is not expected to trigger.

- [ ] **Step 4: Commit**

```bash
git add infra/docker-compose.prod.yml infra/Caddyfile
git commit -m "feat: add prod docker-compose override + Caddy reverse proxy"
```

---

### Task 2: `infra/.env.prod.example` + gitignore

**Files:**
- Create: `infra/.env.prod.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: every env var name the base `infra/docker-compose.yml` and `backend/src/flowsage_backend/config.py` already read (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `NEO4J_USER`, `NEO4J_PASSWORD`, `JWT_SECRET`, `COOKIE_SECURE`, `ANTHROPIC_API_KEY`, `AUTH_RATE_LIMIT_OVERRIDE`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PRO`, `STRIPE_PRICE_ID_TEAM`, `APP_BASE_URL`), plus the new `DOMAIN`, `ENVIRONMENT`, `SECRET_ENCRYPTION_KEY` this deploy needs.
- Produces: `infra/.env.prod.example`, referenced by `infra/deploy.sh` (Task 3) and `infra/DEPLOY.md` (Task 5).

- [ ] **Step 1: Confirm the existing `.gitignore` would swallow the new file**

Run: `git check-ignore -v infra/.env.prod.example || echo "not ignored"`
Expected (before Step 3 below): the file doesn't exist yet, so this errors — that's fine, this step is just confirming the pattern that will apply once it does. The existing rule is `.env.*` (line 25) with only `!.env.example` (line 26) as a negation — that negation matches the literal name `.env.example`, not `.env.prod.example`, so the new file needs its own negation.

- [ ] **Step 2: Add gitignore entries**

Modify `.gitignore` — after the existing `.env`/`.env.*`/`!.env.example` block, add:

```
!infra/.env.prod.example
```

- [ ] **Step 3: Write `infra/.env.prod.example`**

```bash
# Copy to infra/.env.prod on the VPS, fill in real values, then:
#   chmod 600 infra/.env.prod
# Never commit infra/.env.prod -- see .gitignore (this .example file is the
# only tracked copy).

# Public domain this deploy is served under (Caddy TLS target + APP_BASE_URL).
DOMAIN=example.com

# flowsage_backend.config.Settings._reject_placeholder_secret_outside_dev
# hard-fails startup if these are left as dev placeholders once
# ENVIRONMENT != development. Generate real values with: openssl rand -hex 32
ENVIRONMENT=production
JWT_SECRET=
SECRET_ENCRYPTION_KEY=

# Cookies are only sent over HTTPS once this is true -- must be true here
# since Caddy terminates TLS in front of the app.
COOKIE_SECURE=true

# Used to build Stripe Checkout success/cancel URLs and CORS-adjacent checks.
APP_BASE_URL=https://example.com

# Postgres
POSTGRES_USER=flowsage
POSTGRES_PASSWORD=
POSTGRES_DB=flowsage

# Neo4j
NEO4J_USER=neo4j
NEO4J_PASSWORD=

# Persona simulation agent
ANTHROPIC_API_KEY=

# Stripe billing -- leave blank to run fully unconfigured (checkout/portal
# 400 cleanly, per docs/superpowers/specs/2026-07-27-stripe-billing-design.md)
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_ID_PRO=
STRIPE_PRICE_ID_TEAM=

# Dev-only rate-limit bypass -- leave unset in production.
AUTH_RATE_LIMIT_OVERRIDE=
```

- [ ] **Step 4: Verify the file is tracked, not ignored, and the compose stack accepts it**

Run:
```bash
git add -f infra/.env.prod.example  # -f only needed to prove the negation works; drop once .gitignore is committed
git status --short infra/.env.prod.example
git check-ignore -v infra/.env.prod.example; echo "exit=$?"
```
Expected: `git status` shows it staged (`A  infra/.env.prod.example`); `git check-ignore` prints nothing and exits `1` (not ignored).

Then, using the example file's placeholder values as a smoke test that every required var is declared and substitution doesn't error:
```bash
cd /home/asus/Projects/personal/FlowSage
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod.example config >/dev/null
echo "config OK"
```
Expected: `config OK`, no "variable is not set" warnings.

- [ ] **Step 5: Commit**

```bash
git add .gitignore infra/.env.prod.example
git commit -m "feat: add prod env template + gitignore rule"
```

---

### Task 3: `infra/deploy.sh` + full local end-to-end verification

**Files:**
- Create: `infra/deploy.sh`

**Interfaces:**
- Consumes: `infra/docker-compose.prod.yml` (Task 1), `infra/.env.prod` (Task 2's template, copied to a real file for this task's local test run).
- Produces: the deploy entry point `infra/DEPLOY.md` (Task 5) tells operators to run.

- [ ] **Step 1: Write `infra/deploy.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod)

git pull
"${COMPOSE[@]}" up -d --build

echo "Waiting for postgres, redis, neo4j to report healthy..."
timeout=180
for svc in postgres redis neo4j; do
  elapsed=0
  while true; do
    status=$("${COMPOSE[@]}" ps "$svc" --format '{{.Health}}' 2>/dev/null || echo "")
    if [ "$status" = "healthy" ]; then
      break
    fi
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "Timed out waiting for $svc to become healthy (last status: '$status')" >&2
      "${COMPOSE[@]}" logs --tail=50 "$svc" >&2
      exit 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
done

"${COMPOSE[@]}" exec -T backend /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head

echo "Deployed $(git rev-parse --short HEAD) successfully."
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x infra/deploy.sh`

- [ ] **Step 3: Syntax-check it**

Run: `bash -n infra/deploy.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Create a real local `.env.prod` for this end-to-end test run**

This is a throwaway local file, not committed (already gitignored by the `.env.*` rule). `DOMAIN=localhost` makes Caddy issue a locally-trusted cert via its internal CA instead of attempting public Let's Encrypt issuance (which would fail for `localhost`).

Run:
```bash
cd /home/asus/Projects/personal/FlowSage
cp infra/.env.prod.example infra/.env.prod
python3 - <<'EOF'
import re
path = "infra/.env.prod"
values = {
    "DOMAIN": "localhost",
    "ENVIRONMENT": "production",
    "JWT_SECRET": __import__("secrets").token_hex(32),
    "SECRET_ENCRYPTION_KEY": __import__("secrets").token_hex(32),
    "COOKIE_SECURE": "true",
    "APP_BASE_URL": "https://localhost",
    "POSTGRES_PASSWORD": "flowsage_prod_test",
    "NEO4J_PASSWORD": "flowsage_prod_test",
}
with open(path) as f:
    lines = f.readlines()
out = []
for line in lines:
    m = re.match(r"^([A-Z_]+)=", line)
    if m and m.group(1) in values:
        out.append(f"{m.group(1)}={values[m.group(1)]}\n")
    else:
        out.append(line)
with open(path, "w") as f:
    f.writelines(out)
EOF
chmod 600 infra/.env.prod
```

- [ ] **Step 5: Run the real deploy script against this machine**

Run: `./infra/deploy.sh`
Expected: `git pull` runs (no-op if already up to date), images build, all three DB services report healthy within the 180s timeout, the Alembic migration runs and prints its usual upgrade log, and the script ends with `Deployed <sha> successfully.`

If port `80` or `443` is already bound on this machine, `docker compose up` will fail on the `caddy` service — check `sudo ss -tlnp | grep -E ':80|:443'` first and stop whatever's using them (or note the conflict and skip live-porting this step, documenting the finding instead of forcing it).

- [ ] **Step 6: Verify the full chain end-to-end**

Run:
```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/api/billing/usage
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost/
```
Expected: first prints `401` (Caddy → frontend → nginx `/api/` proxy → backend → auth-enforced), second prints `200` (SPA served). `-k` is only because Caddy's *local* CA isn't in curl's trust store on this dev box — a real deploy against a real `DOMAIN` gets a publicly-trusted Let's Encrypt cert and needs no `-k`.

- [ ] **Step 7: Tear down (leave the stack down for Task 4, which brings it back up)**

Run: `docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod down`

- [ ] **Step 8: Commit**

```bash
git add infra/deploy.sh
git commit -m "feat: add deploy.sh"
```

(`infra/.env.prod` stays uncommitted/gitignored — it's this task's local test artifact, reused by Task 4.)

---

### Task 4: `infra/backup-postgres.sh` + restore drill

**Files:**
- Create: `infra/backup-postgres.sh`

**Interfaces:**
- Consumes: `infra/.env.prod` (the local test file from Task 3, still present on disk).
- Produces: the backup entry point `infra/DEPLOY.md` (Task 5) documents a crontab line for.

- [ ] **Step 1: Write `infra/backup-postgres.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BACKUP_DIR="${BACKUP_DIR:-/var/backups/flowsage}"
RETENTION_DAYS=14
COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod)

# shellcheck disable=SC1091
source infra/.env.prod
POSTGRES_USER="${POSTGRES_USER:-flowsage}"
POSTGRES_DB="${POSTGRES_DB:-flowsage}"

mkdir -p "$BACKUP_DIR"
outfile="$BACKUP_DIR/postgres-$(date +%F).sql.gz"

"${COMPOSE[@]}" exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$outfile"
echo "Backup written to $outfile"

find "$BACKUP_DIR" -name 'postgres-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
```

- [ ] **Step 2: Make it executable, syntax-check**

Run: `chmod +x infra/backup-postgres.sh && bash -n infra/backup-postgres.sh`
Expected: no output, exit 0.

- [ ] **Step 3: Bring the stack back up for this task's test**

Run: `./infra/deploy.sh`
Expected: same as Task 3 Step 5 — ends with `Deployed <sha> successfully.`

- [ ] **Step 4: Seed one identifiable row so the backup/restore drill proves something real**

Run:
```bash
COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod)
"${COMPOSE[@]}" exec -T backend /workspace/.venv/bin/flowsage-backend create-user backup-drill@flowsage.dev supersecret123
```
Expected: command succeeds (this user is the "identifiable row" the restore drill below confirms survives).

- [ ] **Step 5: Run the backup script against a local scratch backup directory**

Run:
```bash
BACKUP_DIR=/tmp/flowsage-backup-test ./infra/backup-postgres.sh
ls -la /tmp/flowsage-backup-test/
```
Expected: prints `Backup written to /tmp/flowsage-backup-test/postgres-<today>.sql.gz`; `ls` shows that file with nonzero size.

- [ ] **Step 6: Restore drill — prove the dump is actually restorable**

Run:
```bash
COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod)
"${COMPOSE[@]}" exec -T postgres psql -U flowsage -c "CREATE DATABASE flowsage_restore_test;"
gunzip -c /tmp/flowsage-backup-test/postgres-*.sql.gz | "${COMPOSE[@]}" exec -T postgres psql -U flowsage -d flowsage_restore_test
"${COMPOSE[@]}" exec -T postgres psql -U flowsage -d flowsage_restore_test -c "SELECT email FROM users WHERE email = 'backup-drill@flowsage.dev';"
"${COMPOSE[@]}" exec -T postgres psql -U flowsage -c "DROP DATABASE flowsage_restore_test;"
```
Expected: the `SELECT` returns exactly the one row with `backup-drill@flowsage.dev` — proof the gzip dump round-trips into a working database.

- [ ] **Step 7: Clean up local test artifacts**

Run:
```bash
rm -rf /tmp/flowsage-backup-test
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod down -v
rm -f infra/.env.prod
```

- [ ] **Step 8: Commit**

```bash
git add infra/backup-postgres.sh
git commit -m "feat: add postgres backup script"
```

---

### Task 5: `infra/DEPLOY.md` runbook

**Files:**
- Create: `infra/DEPLOY.md`

**Interfaces:**
- Consumes: every script/file from Tasks 1-4 (`infra/docker-compose.prod.yml`, `infra/Caddyfile`, `infra/.env.prod.example`, `infra/deploy.sh`, `infra/backup-postgres.sh`) — this task only documents commands already proven to work in those tasks' verification steps, it introduces no new commands.

- [ ] **Step 1: Write `infra/DEPLOY.md`**

```markdown
# Deploying FlowSage to a VPS

One-time setup, then repeatable deploys via `deploy.sh`. Assumes a VPS with
a public IP, a domain's DNS `A`/`AAAA` record already pointed at it, and SSH
access.

## First-time setup

1. Install Docker Engine + the Compose plugin (see docs.docker.com/engine/install/ for your distro).
2. Clone the repo onto the server: `git clone <repo-url> flowsage && cd flowsage`
3. `cp infra/.env.prod.example infra/.env.prod`
4. Fill in `infra/.env.prod`: real `DOMAIN`, `openssl rand -hex 32` for `JWT_SECRET` and `SECRET_ENCRYPTION_KEY`, real Postgres/Neo4j passwords, `ANTHROPIC_API_KEY`, and Stripe keys if billing upgrades are live (leave blank otherwise -- checkout/portal degrade to a clean 400 unconfigured).
5. `chmod 600 infra/.env.prod`
6. Confirm ports 80/443 are free: `sudo ss -tlnp | grep -E ':80|:443'` should print nothing.
7. `chmod +x infra/deploy.sh infra/backup-postgres.sh`
8. Add the nightly backup cron job: `crontab -e`, add:
   ```
   0 3 * * * cd /path/to/flowsage && ./infra/backup-postgres.sh >> /var/log/flowsage-backup.log 2>&1
   ```

## Deploying (first time and every update after)

```bash
./infra/deploy.sh
```

This pulls the latest `main`, rebuilds and restarts every service, waits for
Postgres/Redis/Neo4j to report healthy, and runs any pending Alembic
migrations. Safe to re-run if it fails partway.

Verify: `curl -i https://<domain>/api/billing/usage` should return `401`
(confirms TLS + the full Caddy -> frontend -> backend chain).

## Backups

`infra/backup-postgres.sh` runs nightly via the cron entry above: `pg_dump`
piped to `gzip`, written to `/var/backups/flowsage/`, anything older than 14
days deleted automatically. To restore from a backup file:

```bash
gunzip -c /var/backups/flowsage/postgres-YYYY-MM-DD.sql.gz | \
  docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml \
    --env-file infra/.env.prod exec -T postgres psql -U flowsage -d flowsage
```

Run this against a scratch database (`CREATE DATABASE ... ; ... ; DROP
DATABASE ...`, same pattern as the restore drill in this project's deploy
plan) periodically to confirm backups are actually restorable, not just
present.

## Out of scope

No CI auto-deploy (manual SSH only), no Neo4j backups, no blue/green
deploys -- see `docs/superpowers/specs/2026-07-30-vps-deploy-design.md` for
the full rationale.
```

- [ ] **Step 2: Cross-check against Tasks 1-4**

Read back `infra/DEPLOY.md` next to `infra/deploy.sh` and `infra/backup-postgres.sh` and confirm every command mentioned (`deploy.sh`, `backup-postgres.sh`, the restore `gunzip | psql` line, the cron line's script path) matches what those files actually do. No live run needed — this is a docs-only cross-check.

- [ ] **Step 3: Commit**

```bash
git add infra/DEPLOY.md
git commit -m "docs: add VPS deploy runbook"
```

---

## Self-Review Notes (for the plan author, not a task)

- Spec coverage: topology (Task 1), `.env.prod.example`/gitignore (Task 2), `deploy.sh` (Task 3), `backup-postgres.sh` + restore drill (Task 4), `DEPLOY.md` (Task 5), end-to-end curl verification (Task 3 Step 6) — all five spec deliverables have a task.
- The spec's "out of scope" list (real domain, CI auto-deploy, Neo4j backups, blue/green) is deliberately not built anywhere in this plan — confirmed no task drifts into any of them.

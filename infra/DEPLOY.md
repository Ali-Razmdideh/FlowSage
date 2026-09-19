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
DATABASE ...`, same pattern used to verify this project's backup script
during development) periodically to confirm backups are actually
restorable, not just present.

## Out of scope

No CI auto-deploy (manual SSH only), no Neo4j backups, no blue/green deploys --
this is a single-VPS deploy for a small pilot footprint, not a
high-availability target.

## Upgrading encryption keys safely

Production Compose now forces `ENVIRONMENT=production` and secure cookies for
both API and worker, and requires explicit `JWT_SECRET` and
`SECRET_ENCRYPTION_KEY`. Startup rejects development placeholders or secrets
shorter than 32 UTF-8 bytes. Generate independent random values, for example
with `openssl rand -hex 32`.

**Before the first deployment of this security update**, rotate existing
credentials if the previous deployment used the development encryption key.
Earlier Compose files did not pass `SECRET_ENCRYPTION_KEY` to containers, even
when it was present in `infra/.env.prod`; their effective key was
`dev-encryption-key-change-me-before-deploy` unless supplied separately.
Changing JWT_SECRET also invalidates existing login sessions.

Perform rotation during a maintenance window; do not run `deploy.sh` first:

1. Back up Postgres with `infra/backup-postgres.sh` and securely retain the old
   encryption key together with that backup. A pre-rotation backup requires
   the old key when restored.
2. Pull the new code and build the backend and worker images, without starting
   them. Set new, independent JWT/encryption secrets in the restricted
   `infra/.env.prod` file. Keep its permissions at `600`.
3. Stop both backend and worker. Leave Postgres running. Use this Compose prefix
   for build/stop/run commands:
   `docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml --env-file infra/.env.prod`.
4. Read the **actual old encryption key** into a temporary environment variable
   without placing it in shell history:
   ```bash
   read -rsp 'Old encryption key: ' OLD_SECRET_ENCRYPTION_KEY
   echo
   export OLD_SECRET_ENCRYPTION_KEY
   docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml \
     --env-file infra/.env.prod run --rm --no-deps -e OLD_SECRET_ENCRYPTION_KEY \
     backend /workspace/.venv/bin/flowsage-backend rotate-encryption-key --dry-run
   ```
5. After a successful dry run, repeat the same command without `--dry-run`.
   Rotation decrypts raw Jira tokens and webhook secrets and rewrites them in
   one transaction. If any row cannot be decrypted by either key, the entire
   operation rolls back. Already-rotated rows are accepted, so retrying is safe.
   The command prints only credential counts and sanitized validation errors.
6. Run `unset OLD_SECRET_ENCRYPTION_KEY`. While API and worker remain stopped,
   run pending Alembic migrations using the new image:
   ```bash
   docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml \
     --env-file infra/.env.prod run --rm --no-deps backend \
     /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head
   ```
   Then recreate the backend and worker with the new configuration and publish
   the matching frontend image. Verify login, existing API keys, and configured
   integration deliveries.

Rotation is an explicit maintenance command; startup never rotates data.
If rollback is needed after successful rotation, stop API/worker and restore
both the pre-rotation database backup and its matching encryption key. Never
restart old-key processes against a newly rotated database.

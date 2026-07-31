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

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

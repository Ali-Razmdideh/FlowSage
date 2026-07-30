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

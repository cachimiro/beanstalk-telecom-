#!/usr/bin/env bash
# Pull latest code and restart services with zero-downtime rolling update.
# Run from the project root: bash scripts/update.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Rebuilding React frontend"
docker compose --profile build run --rm frontend-build

echo "==> Running database migrations"
docker compose run --rm api alembic -c migrations/alembic.ini upgrade head

echo "==> Rebuilding and restarting api and worker"
docker compose up -d --build api worker

echo "==> Reloading nginx"
docker compose exec nginx nginx -s reload

echo "==> Update complete."

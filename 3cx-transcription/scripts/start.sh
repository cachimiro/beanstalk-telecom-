#!/usr/bin/env bash
# Start (or restart) the full stack on the Droplet.
# Run from the project root: bash scripts/start.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example to .env and fill in values."
  exit 1
fi

echo "==> Building React frontend"
docker compose --profile build run --rm frontend-build

echo "==> Running database migrations"
docker compose run --rm api alembic -c migrations/alembic.ini upgrade head

echo "==> Starting services"
docker compose up -d --build postgres redis api worker nginx

echo "==> Waiting for API to be healthy"
sleep 5
for i in {1..12}; do
  if curl -sf http://localhost/api/health > /dev/null 2>&1; then
    echo "==> API is healthy"
    break
  fi
  echo "   Waiting… ($i/12)"
  sleep 5
done

echo ""
echo "==> Stack is running."
echo ""
echo "Create your first admin user:"
echo "  docker compose exec api python /app/../scripts/create_admin.py admin@example.com yourpassword"
echo ""
echo "Admin dashboard: http://$(curl -sf http://checkip.amazonaws.com || echo 'YOUR_DROPLET_IP')/admin/users"
echo ""
echo "Pub/Sub webhook URL to configure in GCP:"
echo "  http://$(curl -sf http://checkip.amazonaws.com || echo 'YOUR_DROPLET_IP')/webhook/gcs"

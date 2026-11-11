#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
command -v docker >/dev/null || { printf 'Docker is required\n' >&2; exit 1; }
command -v python3 >/dev/null || { printf 'Python 3 is required for configuration checks\n' >&2; exit 1; }
docker compose version >/dev/null
test -f .env || { printf 'Create .env from .env.production.example first\n' >&2; exit 1; }
chmod 600 .env
python3 scripts/check_env.py .env
compose=(docker compose -f compose.production.yaml)
"${compose[@]}" config --quiet
mkdir -p .deploy-state
previous_image="$("${compose[@]}" images -q api | head -n 1)"
if [[ -n "$previous_image" ]]; then
    rollback_tag="platezhnik:rollback-$(date -u +%Y%m%dT%H%M%SZ)"
    docker tag "$previous_image" "$rollback_tag"
    printf '%s\n' "$rollback_tag" > .deploy-state/previous-image
fi
"${compose[@]}" build api
"${compose[@]}" run --rm --no-deps api python -m app.preflight
"${compose[@]}" up -d --wait db

# A failed backup or test stops deployment before existing application processes are stopped.
if "${compose[@]}" exec -T db psql -U platezhnik -d platezhnik -tAc "SELECT to_regclass('public.recurring_payments') IS NOT NULL" | grep -qx t; then
    bash scripts/backup.sh
fi
docker compose -f compose.yaml --profile test run --build --rm tests
docker compose -f compose.yaml --profile test stop test-db
docker compose -f compose.yaml --profile test rm -f test-db

# Brief maintenance window: avoid concurrent old/new writes during migrations.
printf 'Starting maintenance window: API, worker and bot will stop briefly.\n'
"${compose[@]}" stop api worker bot
if "${compose[@]}" exec -T db psql -U platezhnik -d platezhnik -tAc "SELECT to_regclass('public.recurring_payments') IS NOT NULL" | grep -qx t; then
    bash scripts/backup.sh
fi
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" up -d --wait --wait-timeout 180 api worker bot
"${compose[@]}" exec -T api python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status == 200"
domain="$(python3 -c "from pathlib import Path; from scripts.check_env import validate; print(validate(Path('.env'))['APP_DOMAIN'])")"
curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors "https://$domain/health"
printf '\nHTTPS checked. Check the bot and a real reminder as described in DEPLOY.md.\n'

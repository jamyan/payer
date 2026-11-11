#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
backup_file="${1:?Usage: bash scripts/verify_backup.sh /path/to/backup.dump}"
test -s "$backup_file"
compose=(docker compose -f compose.production.yaml)
check_db="restorecheck_$(date -u +%Y%m%d%H%M%S)_$$"
[[ "$check_db" =~ ^restorecheck_[0-9]+_[0-9]+$ ]]
"${compose[@]}" exec -T db createdb -U platezhnik "$check_db"
cleanup() { "${compose[@]}" exec -T db dropdb -U platezhnik --if-exists "$check_db"; }
trap cleanup EXIT
"${compose[@]}" exec -T db pg_restore -U platezhnik --no-owner --single-transaction -d "$check_db" < "$backup_file"
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U platezhnik -d "$check_db" -c 'SELECT version_num FROM alembic_version; SELECT count(*) AS occurrences FROM occurrences; SELECT count(*) AS payments FROM payments;'
printf 'Restore verified in isolated database; production data untouched.\n'

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
backup_dir="${BACKUP_DIR:-$project_dir/backups}"
mkdir -p -- "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/platezhnik-$stamp.dump"
temporary="$target.partial"
trap 'rm -f -- "$temporary"' EXIT
docker compose -f compose.production.yaml exec -T db pg_dump -U platezhnik -d platezhnik -Fc > "$temporary"
test -s "$temporary"
# Check archive structure before making the file available as a completed backup.
docker compose -f compose.production.yaml exec -T db pg_restore --list < "$temporary" > /dev/null
mv -- "$temporary" "$target"
sha256sum "$target" > "$target.sha256"
printf 'Backup saved: %s\n' "$target"
# Existing backups are deliberately kept. Retention and off-server copies are operator decisions.

#!/usr/bin/env bash
# scripts/backup_d1.sh — nightly logical backup of the IATIS D1 database.
#
# Production audit item H5: the only durable copy of decisions/outcomes/
# experiences was D1 itself; there was no backup or restore procedure.
# This exports a full SQL dump via wrangler, keeps N days locally, and
# (optionally) mirrors to any rclone remote (R2, S3, B2, ...).
#
# Requirements on the host that runs this (VPS or laptop):
#   - node + wrangler (npm i -g wrangler), authenticated either via
#     `wrangler login` or a CLOUDFLARE_API_TOKEN env var whose token has
#     the "D1:Edit" permission. Do NOT hardcode the token here.
#   - run from anywhere; paths are resolved relative to this script.
#
# Usage:
#   scripts/backup_d1.sh                 # dump to backups/d1/ and prune
#   BACKUP_RCLONE_REMOTE=r2:iatis-backups scripts/backup_d1.sh   # + upload
#
# Restore procedure (REHEARSE THIS — a backup that has never been
# restored is a hope, not a backup):
#   wrangler d1 create iatis-restore-test
#   wrangler d1 execute iatis-restore-test --remote --file=backups/d1/iatis-YYYY-MM-DD.sql
#   # verify row counts, then point D1_WORKER_URL's Worker binding at it
#   # (cloudflare/wrangler.toml database_id) if promoting the restore.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_NAME="${D1_DB_NAME:-iatis}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups/d1}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y-%m-%d)"
OUT="$BACKUP_DIR/${DB_NAME}-${STAMP}.sql"

mkdir -p "$BACKUP_DIR"

echo "[backup_d1] exporting D1 database '$DB_NAME' -> $OUT"
# --remote: dump the real production database, not any local sim.
(cd "$REPO_ROOT/cloudflare" && npx wrangler d1 export "$DB_NAME" --remote --output="$OUT")

# A dump that didn't produce CREATE TABLE statements is not a backup.
if ! grep -q "CREATE TABLE" "$OUT"; then
  echo "[backup_d1] ERROR: $OUT contains no schema — export failed?" >&2
  exit 1
fi
gzip -f "$OUT"
echo "[backup_d1] wrote ${OUT}.gz ($(du -h "${OUT}.gz" | cut -f1))"

# Prune local dumps older than KEEP_DAYS.
find "$BACKUP_DIR" -name "${DB_NAME}-*.sql.gz" -mtime +"$KEEP_DAYS" -delete

# Optional off-site mirror. Configure once with `rclone config`.
if [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
  echo "[backup_d1] mirroring to $BACKUP_RCLONE_REMOTE"
  rclone copy "$BACKUP_DIR" "$BACKUP_RCLONE_REMOTE" --include "${DB_NAME}-*.sql.gz"
fi

echo "[backup_d1] done"

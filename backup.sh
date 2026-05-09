#!/usr/bin/env bash
# C10: Backup hugo-mcp config + content + TLS certs → GPG-encrypted tar
# Usage: ./backup.sh [/path/to/output/dir]
# Requires: gpg key for backup recipient, BACKUP_RECIPIENT env var or .env

set -euo pipefail

BACKUP_DIR="${1:-/home/jm/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE="/tmp/hugo-mcp-backup-${TIMESTAMP}.tar.gz"
ENCRYPTED="${BACKUP_DIR}/hugo-mcp-backup-${TIMESTAMP}.tar.gz.gpg"
RETENTION_DAYS=30

# Load recipient from .env if not set
if [[ -z "${BACKUP_RECIPIENT:-}" ]]; then
    source /home/jm/hugo-mcp/.env 2>/dev/null || true
fi
BACKUP_RECIPIENT="${BACKUP_RECIPIENT:-}"

if [[ -z "$BACKUP_RECIPIENT" ]]; then
    echo "ERROR: BACKUP_RECIPIENT not set (GPG key fingerprint or email)" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting backup..."

# Bundle: hugo-mcp config + tokens + TLS cert (NOT key — keep it local)
tar --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='venv/' \
    --exclude='tls/server.key' \
    -czf "$ARCHIVE" \
    -C /home/jm \
    hugo-mcp/main.py \
    hugo-mcp/token_mgr.py \
    hugo-mcp/backup.sh \
    hugo-mcp/requirements.txt \
    hugo-mcp/requirements.lock \
    hugo-mcp/tokens.json \
    hugo-mcp/tls/server.crt \
    hugo-mcp/docs/ \
    hugo-mcp/.env \
    hugo-site/content/ \
    hugo-site/hugo.toml \
    hugo-site/assets/ \
    2>/dev/null

SIZE=$(stat -c%s "$ARCHIVE")
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Archive: ${SIZE} bytes"

# GPG encrypt
gpg --yes --batch --recipient "$BACKUP_RECIPIENT" \
    --output "$ENCRYPTED" --encrypt "$ARCHIVE"
rm -f "$ARCHIVE"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Encrypted backup: $ENCRYPTED"

# Purge old backups
find "$BACKUP_DIR" -name "hugo-mcp-backup-*.tar.gz.gpg" \
    -mtime "+${RETENTION_DAYS}" -delete -print | \
    sed "s/^/[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Purged: /"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup complete."

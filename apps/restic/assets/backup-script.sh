#!/bin/sh
set -euo pipefail

echo "Starting restic backup at $(date)"

# Initialize restic repository if it doesn't exist
echo "Initializing restic repository..."
restic snapshots || restic init

# Read backup paths from configmap
BACKUP_PATHS_FILE="/config/backup-paths.txt"

if [[ ! -f "$BACKUP_PATHS_FILE" ]]; then
    echo "ERROR: Backup paths file not found at $BACKUP_PATHS_FILE"
    exit 1
fi

echo "Reading backup paths from $BACKUP_PATHS_FILE"
PATHS=$(cat "$BACKUP_PATHS_FILE" | grep -v '^$' | tr '\n' ' ')

if [[ -z "$PATHS" ]]; then
    echo "WARNING: No backup paths found in configmap"
    exit 0
fi

echo "Backup paths: $PATHS"

# Perform backup
echo "Starting backup of paths: $PATHS"
restic backup $PATHS \
    --tag "homelab-$(date +%Y%m%d)" \
    --exclude-caches \
    --one-file-system

# Check backup
echo "Verifying backup integrity..."
restic check --read-data-subset=5%

# Cleanup old snapshots (keep last 30 daily, 12 monthly, 7 weekly)
echo "Cleaning up old snapshots..."
restic forget \
    --keep-daily 30 \
    --keep-weekly 7 \
    --keep-monthly 12 \
    --prune

echo "Backup completed successfully at $(date)"
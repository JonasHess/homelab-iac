#!/bin/sh
set -euo pipefail

echo "Starting restic restore at $(date)"
echo "Restore date: $RESTORE_DATE"

# Generate timestamp for this restore
RESTORE_TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
RESTORE_DIR="/restored-data/$RESTORE_TIMESTAMP"

echo "Restore directory: $RESTORE_DIR"

# Create restore directory
mkdir -p "$RESTORE_DIR"

# Read backup paths from configmap (but filter for restore flag)
BACKUP_PATHS_FILE="/config/backup-paths.txt"

if [[ ! -f "$BACKUP_PATHS_FILE" ]]; then
    echo "ERROR: Backup paths file not found at $BACKUP_PATHS_FILE"
    exit 1
fi

echo "Reading backup paths from $BACKUP_PATHS_FILE"
echo "File contents:"
cat "$BACKUP_PATHS_FILE"
echo "--- End of file contents ---"

# Convert YYYY-MM-DD to YYYYMMDD format for tag matching
BACKUP_TAG_DATE=$(echo "$RESTORE_DATE" | sed 's/-//g')

# Get list of snapshots for the specified date
echo "Finding snapshots for date: $RESTORE_DATE (tag: homelab-$BACKUP_TAG_DATE)"
restic snapshots --tag "homelab-$BACKUP_TAG_DATE" --json > /tmp/snapshots.json

# Check if any snapshots exist for this date
SNAPSHOT_COUNT=$(cat /tmp/snapshots.json | wc -l)
if [ "$SNAPSHOT_COUNT" -eq 0 ] || [ "$(cat /tmp/snapshots.json)" = "null" ]; then
    echo "ERROR: No snapshots found for date $RESTORE_DATE"
    echo "Available snapshots:"
    restic snapshots
    exit 1
fi

echo "Found snapshots for $RESTORE_DATE:"
restic snapshots --tag "homelab-$BACKUP_TAG_DATE"

# For each path in backup-paths.txt, check if it has a snapshot for this date
while IFS= read -r path; do
    if [ -z "$path" ] || [ "${path#\#}" != "$path" ]; then
        # Skip empty lines and comments
        continue
    fi
    
    echo "Checking for snapshot of path: $path"
    
    # Debug: Show raw output
    echo "Debug - Raw restic output:"
    restic snapshots --tag "homelab-$BACKUP_TAG_DATE" --path "$path" --latest=1
    echo "Debug - Filtered output:"
    restic snapshots --tag "homelab-$BACKUP_TAG_DATE" --path "$path" --latest=1 | grep -v "^ID\|^---\|^$"
    
    # Find the latest snapshot for this specific path and date
    SNAPSHOT_ID=$(restic snapshots --tag "homelab-$BACKUP_TAG_DATE" --path "$path" --latest=1 | grep -v "^ID\|^---\|^$\|snapshots$" | tail -n 1 | awk '{print $1}' || echo "")
    
    echo "Debug - Extracted SNAPSHOT_ID: '$SNAPSHOT_ID'"
    
    if [ -z "$SNAPSHOT_ID" ]; then
        echo "ERROR: No backup found for path '$path' on date $RESTORE_DATE"
        echo "Available snapshots for this path:"
        restic snapshots --path "$path"
        exit 1
    fi
    
    echo "Found snapshot $SNAPSHOT_ID for path $path"
    
    # Create target directory structure
    TARGET_DIR="$RESTORE_DIR$(dirname "$path")"
    mkdir -p "$TARGET_DIR"
    
    # Restore this specific path
    echo "Restoring $path to $RESTORE_DIR$path"
    restic restore "$SNAPSHOT_ID" --target "$RESTORE_DIR" --include "$path"
    
done < "$BACKUP_PATHS_FILE"

echo "Restore completed successfully at $(date)"
echo "Restored data available at: $RESTORE_DIR"
echo ""
echo "To use restored data:"
echo "1. Review files in $RESTORE_DIR"
echo "2. Stop your application pods"
echo "3. Copy needed files from $RESTORE_DIR to your application data directories"
echo "4. Restart your application pods"
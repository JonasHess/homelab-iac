#!/bin/sh
set -euo pipefail

echo "Starting restic backup at $(date)"

# Initialize restic repository if it doesn't exist
echo "Initializing restic repository..."
restic snapshots || restic init

# Check for backup configuration files
BACKUP_CONFIG_FILE="/config/backup-config.yaml"
BACKUP_PATHS_FILE="/config/backup-paths.txt"

if [[ ! -f "$BACKUP_CONFIG_FILE" ]]; then
    echo "WARNING: New backup config file not found at $BACKUP_CONFIG_FILE"
    echo "Falling back to legacy backup-paths.txt"
    
    if [[ ! -f "$BACKUP_PATHS_FILE" ]]; then
        echo "ERROR: Legacy backup paths file not found at $BACKUP_PATHS_FILE"
        exit 1
    fi
    
    # Legacy backup logic
    echo "Reading backup paths from $BACKUP_PATHS_FILE"
    PATHS=$(cat "$BACKUP_PATHS_FILE" | grep -v '^$' | grep -v '^#' | tr '\n' ' ' | sed 's/[[:space:]]*$//')
    
    if [ -z "$PATHS" ]; then
        echo "WARNING: No backup paths found"
        exit 0
    fi
    
    echo "Starting legacy backup of paths: $PATHS"
    restic backup $PATHS \
        --tag "homelab-$(date +%Y%m%d)" \
        --exclude-caches \
        --one-file-system
else
    echo "Using enhanced backup configuration from $BACKUP_CONFIG_FILE"
    
    # Check if yq is available
    if ! command -v yq >/dev/null 2>&1; then
        echo "ERROR: yq is required for enhanced backup configuration but not found"
        echo "Please install yq or use legacy backup configuration"
        exit 1
    fi
    
    # Create temporary directory for filter files
    TEMP_DIR=$(mktemp -d)
    trap "rm -rf $TEMP_DIR" EXIT
    
    # Extract global excludes
    GLOBAL_EXCLUDES_FILE="$TEMP_DIR/global-excludes.txt"
    yq eval '.globalExcludes[]' "$BACKUP_CONFIG_FILE" > "$GLOBAL_EXCLUDES_FILE" 2>/dev/null || touch "$GLOBAL_EXCLUDES_FILE"
    
    echo "Global excludes:"
    cat "$GLOBAL_EXCLUDES_FILE" || echo "(none)"
    echo "--- End of global excludes ---"
    
    # Process each backup configuration
    BACKUP_COUNT=$(yq eval '.backups | length' "$BACKUP_CONFIG_FILE" 2>/dev/null || echo "0")
    
    if [ "$BACKUP_COUNT" -eq 0 ]; then
        echo "WARNING: No backup configurations found"
        echo "This means no applications have backup enabled in their pvcMounts configuration"
        exit 0
    fi
    
    echo "Found $BACKUP_COUNT backup configurations"
    
    # Process each backup path individually with its specific rules
    for i in $(seq 0 $((BACKUP_COUNT - 1))); do
        PATH_CONFIG=$(yq eval ".backups[$i]" "$BACKUP_CONFIG_FILE")
        BACKUP_PATH=$(echo "$PATH_CONFIG" | yq eval '.path' -)
        APP_NAME=$(echo "$PATH_CONFIG" | yq eval '.app' -)
        MOUNT_NAME=$(echo "$PATH_CONFIG" | yq eval '.mount' -)
        
        echo ""
        echo "Processing backup for $APP_NAME/$MOUNT_NAME: $BACKUP_PATH"
        
        # Create hash for unique filter files
        PATH_HASH=$(echo "$BACKUP_PATH" | md5sum | cut -d' ' -f1)
        EXCLUDE_FILE="$TEMP_DIR/exclude-$PATH_HASH.txt"
        INCLUDE_FILE="$TEMP_DIR/include-$PATH_HASH.txt"
        
        # Start with global excludes
        cp "$GLOBAL_EXCLUDES_FILE" "$EXCLUDE_FILE"
        
        # Add path-specific excludes
        echo "$PATH_CONFIG" | yq eval '.exclude[]' - >> "$EXCLUDE_FILE" 2>/dev/null || true
        
        # Create include file if includes are specified
        echo "$PATH_CONFIG" | yq eval '.include[]' - > "$INCLUDE_FILE" 2>/dev/null || rm -f "$INCLUDE_FILE"
        
        # Build restic command arguments
        RESTIC_ARGS=""
        INCLUDE_FILES_FILE=""
        
        # Add exclude file if it has content
        if [ -s "$EXCLUDE_FILE" ]; then
            echo "Exclude patterns for $BACKUP_PATH:"
            cat "$EXCLUDE_FILE"
            RESTIC_ARGS="$RESTIC_ARGS --exclude-file $EXCLUDE_FILE"
        fi
        
        # Add include file if it exists and has content
        if [ -f "$INCLUDE_FILE" ] && [ -s "$INCLUDE_FILE" ]; then
            echo "Include patterns for $BACKUP_PATH:"
            cat "$INCLUDE_FILE"
            # Convert include patterns to files-from format (prepend path)
            INCLUDE_FILES_FILE="$TEMP_DIR/files-from-$PATH_HASH.txt"
            sed "s|^|$BACKUP_PATH/|" "$INCLUDE_FILE" > "$INCLUDE_FILES_FILE"
            RESTIC_ARGS="$RESTIC_ARGS --files-from $INCLUDE_FILES_FILE"
        fi
        
        # Add size-based exclusions
        EXCLUDE_LARGER_THAN=$(echo "$PATH_CONFIG" | yq eval '.excludeLargerThan' - 2>/dev/null | grep -v "null" || echo "")
        if [ -n "$EXCLUDE_LARGER_THAN" ]; then
            echo "Excluding files larger than: $EXCLUDE_LARGER_THAN"
            RESTIC_ARGS="$RESTIC_ARGS --exclude-larger-than $EXCLUDE_LARGER_THAN"
        fi
        
        # Add cache exclusions
        EXCLUDE_CACHES=$(echo "$PATH_CONFIG" | yq eval '.excludeCaches' - 2>/dev/null | grep -v "null" || echo "false")
        if [ "$EXCLUDE_CACHES" = "true" ]; then
            echo "Excluding cache directories"
            RESTIC_ARGS="$RESTIC_ARGS --exclude-caches"
        fi
        
        # Add exclude-if-present
        EXCLUDE_IF_PRESENT=$(echo "$PATH_CONFIG" | yq eval '.excludeIfPresent' - 2>/dev/null | grep -v "null" || echo "")
        if [ -n "$EXCLUDE_IF_PRESENT" ]; then
            echo "Excluding directories containing: $EXCLUDE_IF_PRESENT"
            RESTIC_ARGS="$RESTIC_ARGS --exclude-if-present $EXCLUDE_IF_PRESENT"
        fi
        
        echo "Starting backup of $BACKUP_PATH with args: $RESTIC_ARGS"
        
        # Execute backup with dynamic arguments
        if [ -n "$INCLUDE_FILES_FILE" ] && [ -f "$INCLUDE_FILES_FILE" ]; then
            # When using --files-from, don't specify the path as positional argument
            eval "restic backup $RESTIC_ARGS --tag 'homelab-$(date +%Y%m%d)' --one-file-system"
        else
            # Normal path-based backup
            eval "restic backup '$BACKUP_PATH' $RESTIC_ARGS --tag 'homelab-$(date +%Y%m%d)' --one-file-system"
        fi
        
        echo "Completed backup of $BACKUP_PATH"
    done
fi

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
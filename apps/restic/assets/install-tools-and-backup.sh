#!/bin/sh
set -euo pipefail

echo "Installing required tools..."

# Install yq if not available
if ! command -v yq >/dev/null 2>&1; then
    echo "Installing yq..."
    # Download yq binary for alpine linux
    wget -q -O /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
    chmod +x /usr/local/bin/yq
    echo "yq installed successfully"
else
    echo "yq already available"
fi

# Install additional tools if needed
if ! command -v md5sum >/dev/null 2>&1; then
    echo "Installing coreutils for md5sum..."
    apk add --no-cache coreutils
fi

echo "All tools installed, running backup script..."

# Execute the main backup script
exec /scripts/backup-script.sh
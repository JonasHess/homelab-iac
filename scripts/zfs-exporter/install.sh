#!/bin/bash

# Summary: This script automates the installation and configuration of the ZFS Exporter, a tool
# used to export ZFS metrics for Prometheus.

# A failed download or extraction must stop here, before the service is
# (re)enabled with missing or stale files.
set -euo pipefail

# Variables
TARGET_DIR="/etc/zfsexporter"
SERVICE_NAME="zfs_exporter"

# Always installs the newest release; the script is run by hand when setting up a host.
ZFS_EXPORTER_VERSION=$(wget -qO- https://api.github.com/repos/pdf/zfs_exporter/releases/latest \
  | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p')
if [ -z "$ZFS_EXPORTER_VERSION" ]; then
  echo "Could not determine the latest zfs_exporter release" >&2
  exit 1
fi
FULL_DOWNLOAD_URL="https://github.com/pdf/zfs_exporter/releases/download/v${ZFS_EXPORTER_VERSION}/zfs_exporter-${ZFS_EXPORTER_VERSION}.linux-amd64.tar.gz"

# Create the target directory
echo "Creating target directory: $TARGET_DIR"
sudo mkdir -p "$TARGET_DIR"

# Download the release
echo "Downloading the latest release from: $FULL_DOWNLOAD_URL"
wget -q --show-progress -O /tmp/zfs_exporter.tar.gz "$FULL_DOWNLOAD_URL"

# Extract the archive
echo "Extracting the archive to a temporary location"
# A fresh directory per run: a reused one would still hold files of an older
# release, and the glob below would move those as well.
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
tar -xvzf /tmp/zfs_exporter.tar.gz -C "$TEMP_DIR"

# Move extracted files to the target directory
echo "Moving files from extracted directory to $TARGET_DIR"
sudo mv "$TEMP_DIR"/*/* "$TARGET_DIR"

# Set executable permissions
echo "Setting executable permissions for zfs_exporter"
sudo chmod +x "$TARGET_DIR/zfs_exporter"

# Clean up the temporary file
rm /tmp/zfs_exporter.tar.gz

# Create a systemd service file
echo "Creating a systemd service file for $SERVICE_NAME"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=ZFS Exporter for Prometheus
After=network.target

[Service]
ExecStart=${TARGET_DIR}/zfs_exporter
WorkingDirectory=${TARGET_DIR}
User=root
Restart=always

[Install]
WantedBy=multi-user.target
EOL

# Reload systemd to register the service
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable and start the service
echo "Enabling and starting the $SERVICE_NAME service..."
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME

# Check service status
echo "Checking the status of $SERVICE_NAME service..."
sudo systemctl status $SERVICE_NAME --no-pager

echo "ZFS Exporter installation and systemd service setup completed."

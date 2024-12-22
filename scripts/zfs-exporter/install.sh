#!/bin/bash

# Variables
TARGET_DIR="/etc/zfsexporter"
SERVICE_NAME="zfs_exporter"

FULL_DOWNLOAD_URL="https://github.com/pdf/zfs_exporter/releases/download/v2.3.4/zfs_exporter-2.3.4.linux-amd64.tar.gz"

# Create the target directory
echo "Creating target directory: $TARGET_DIR"
sudo mkdir -p "$TARGET_DIR"

# Download the release
echo "Downloading the latest release from: $FULL_DOWNLOAD_URL"
wget -q --show-progress -O /tmp/zfs_exporter.tar.gz "$FULL_DOWNLOAD_URL"

# Extract the archive
echo "Extracting the archive to a temporary location"
TEMP_DIR="/tmp/zfs_exporter_extract"
mkdir -p "$TEMP_DIR"
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

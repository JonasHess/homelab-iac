#!/bin/bash
set -e  # Exit on first error
set -o pipefail  # Fail if any command in a pipe fails
set -u  # Treat unset variables as an error
cd "$(dirname "$0")" # Change to the script directory

# Summary: This script creates a directory structure for media storage with specific permissions.
BASE_DIR="/mnt/hdd-z1/encrypted/media"
# LinuxServer containers (SABnzbd, Radarr, Sonarr, and others) run as abc
# with UID/GID 911 by default. Keep the media tree writable by that shared
# service identity. Override these values explicitly if the containers change.
MEDIA_SERVICE_UID="${MEDIA_SERVICE_UID:-911}"
MEDIA_SERVICE_GID="${MEDIA_SERVICE_GID:-911}"
MEDIA_SERVICE_OWNER="${MEDIA_SERVICE_UID}:${MEDIA_SERVICE_GID}"

declare -A DIRS_SUBDIRS
DIRS_SUBDIRS=(
  ["apps"]=""
  ["audiobooks"]=""
  ["books"]="comics ebooks magazines literature"
  ["games"]=""
  ["movies"]=""
  ["music"]=""
  ["nzb"]=""
  ["torrent"]=""
  ["_unknown"]="sonar radarr prowlarr manual readarr qbittorrent sabnzbd"
  ["tv-shows"]=""
  ["tutorials"]=""
)

# Ensure the base directory is owned by the shared media-service identity.
echo "Setting up base directory with media-service ownership and permissions..."
sudo mkdir -p "$BASE_DIR"
sudo chown "$MEDIA_SERVICE_OWNER" "$BASE_DIR"
sudo chmod 755 "$BASE_DIR"
echo "Base directory '$BASE_DIR' created with owner '$MEDIA_SERVICE_OWNER' and permissions set to 755."

# Loop through the associative array to create directories and subdirectories
for DIR in "${!DIRS_SUBDIRS[@]}"; do
  sudo mkdir -p "$BASE_DIR/$DIR"
  sudo chown "$MEDIA_SERVICE_OWNER" "$BASE_DIR/$DIR"

  if [ -n "${DIRS_SUBDIRS[$DIR]}" ]; then
    # Set permissions for the main directory (protected)
    sudo chmod 755 "$BASE_DIR/$DIR"
    echo "Directory '$BASE_DIR/$DIR' created with permissions set to 755 (protected)."

    # Creating subdirectories with 777 permissions
    echo "Creating subdirectories in '$DIR' with open read/write permissions..."
    for SUBDIR in ${DIRS_SUBDIRS[$DIR]}; do
      sudo mkdir -p "$BASE_DIR/$DIR/$SUBDIR"
      sudo chown "$MEDIA_SERVICE_OWNER" "$BASE_DIR/$DIR/$SUBDIR"
      sudo chmod 777 "$BASE_DIR/$DIR/$SUBDIR"
      echo "Subdirectory '$BASE_DIR/$DIR/$SUBDIR' created with permissions set to 777."
    done
  else
    # Set permissions for directories without subdirectories (open)
    sudo chmod 777 "$BASE_DIR/$DIR"
    echo "Directory '$BASE_DIR/$DIR' created with permissions set to 777."
  fi
done

echo "All directories and subdirectories created with appropriate permissions."

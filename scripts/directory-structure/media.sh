#!/bin/bash

BASE_DIR="/mnt/tank0/encrypted/media"

declare -A DIRS_SUBDIRS
DIRS_SUBDIRS=(
  ["apps"]=""
  ["audiobooks"]=""
  ["books"]="comics ebooks magazines literature"
  ["games"]=""
  ["movies"]=""
  ["music"]=""
  ["nzb"]=""
  ["_unknown"]="sonar radarr prowlarr manual readarr"
  ["tv-shows"]=""
  ["tutorials"]=""
)

# Ensure the base directory is owned by root and has strict permissions
echo "Setting up base directory with root ownership and permissions..."
sudo mkdir -p "$BASE_DIR"
sudo chown root:root "$BASE_DIR"
sudo chmod 755 "$BASE_DIR"
echo "Base directory '$BASE_DIR' created with root ownership and permissions set to 755."

# Loop through the associative array to create directories and subdirectories
for DIR in "${!DIRS_SUBDIRS[@]}"; do
  sudo mkdir -p "$BASE_DIR/$DIR"
  sudo chown root:root "$BASE_DIR/$DIR"

  if [ -n "${DIRS_SUBDIRS[$DIR]}" ]; then
    # Set permissions for the main directory (protected)
    sudo chmod 755 "$BASE_DIR/$DIR"
    echo "Directory '$BASE_DIR/$DIR' created with permissions set to 755 (protected)."

    # Creating subdirectories with 777 permissions
    echo "Creating subdirectories in '$DIR' with open read/write permissions..."
    for SUBDIR in ${DIRS_SUBDIRS[$DIR]}; do
      sudo mkdir -p "$BASE_DIR/$DIR/$SUBDIR"
      sudo chown root:root "$BASE_DIR/$DIR/$SUBDIR"
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

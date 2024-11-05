#!/bin/bash

set -e

handle_error() {
    echo "Fehler in Zeile $1"
    exit 1
}

trap 'handle_error $LINENO' ERR

# Reinstall the kernel headers for the current kernel version
sudo apt reinstall -y linux-headers-$(uname -r)

# Reinstall the pivccu modules
sudo DEBIAN_FRONTEND=noninteractive apt reinstall -y pivccu-modules-dkms

# Start the pivccu dkms service
sudo service pivccu-dkms start

# Load the eq3_char_loop module
sudo modprobe eq3_char_loop

echo "Script execution completed successfully."
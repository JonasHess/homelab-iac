#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Function to display the help message
show_help() {
  echo "Usage: $0 <ip-range> [--help]"
  echo
  echo "Arguments:"
  echo "  <ip-range>  The IP range for MetalLB."
  echo "  --help      Display this help message."
  exit 0
}

# Function to ask for user confirmation
confirm() {
  read -r -p "${1:-Are you sure? [y/N]} " response
  case "$response" in
    [yY][eE][sS]|[yY])
      true
      ;;
    *)
      false
      ;;
  esac
}

# Check if the help command is provided
if [[ "$1" == "--help" ]]; then
  show_help
fi

# Check if the IP range parameter is provided
if [ -z "$1" ]; then
  echo "Error: IP range parameter is missing!"
  echo "Usage: $0 <ip-range>"
  exit 1
fi

IP_RANGE=$1

if ! command -v kubectl &> /dev/null; then
  echo "kubectl could not be found. Installing kubectl..."
  snap install kubectl --classic
fi


ensure_microk8s_installed() {
  echo "Checking if microk8s is already installed..."
  if snap list | grep -q microk8s; then
    echo "microk8s is already installed."
    if confirm "Do you want to uninstall microk8s? [y/N]"; then
      microk8s stop
      snap remove microk8s
      install_microk8s
    else
      echo "Skipping microk8s installation."
    fi
  else
    install_microk8s
  fi
}

install_microk8s() {
  echo "Installing microk8s..."
  snap install microk8s --classic --channel=1.34/stable
  microk8s enable dns
  microk8s enable metallb:$IP_RANGE

  microk8s enable metrics-server
  microk8s enable hostpath-storage

  if confirm "Do you want to enable NVIDIA GPU support? [y/N]"; then
    echo "=== Setting up NVIDIA GPU support ==="
    
    # Check for NVIDIA kernel modules
    echo "Checking for NVIDIA kernel modules..."
    if ! lsmod | grep -q "^nvidia "; then
      echo "ERROR: NVIDIA kernel modules not loaded. Please install NVIDIA drivers on the host first."
      echo "Skipping GPU support."
    else
      echo "NVIDIA kernel modules found."
      
      # Get driver version from kernel module
      DRIVER_VERSION=$(cat /proc/driver/nvidia/version | grep "NVIDIA" | head -1 | awk '{print $8}')
      echo "Detected driver version: $DRIVER_VERSION"
      
      # Check if nvidia-smi is available, install if missing
      if ! command -v nvidia-smi &> /dev/null; then
        echo "nvidia-smi not found. Installing nvidia-utils..."
        # Try to detect the driver series from version number
        DRIVER_SERIES=$(echo $DRIVER_VERSION | cut -d. -f1)
        sudo apt-get update
        # Try to install utils matching the driver version
        sudo apt-get install -y nvidia-utils-${DRIVER_SERIES}-server || sudo apt-get install -y nvidia-utils-${DRIVER_SERIES} || {
          echo "WARNING: Could not auto-install nvidia-utils. Please install manually."
        }
      fi
      
      # Verify nvidia-smi works
      echo "Verifying nvidia-smi..."
      if ! nvidia-smi &> /dev/null; then
        echo "WARNING: nvidia-smi failed to run. GPU addon may not work properly."
      else
        echo "nvidia-smi working."
        nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
      fi
      
      # Disable GPU addon if already enabled
      echo "Disabling GPU addon if previously enabled..."
      microk8s disable gpu 2>/dev/null || true
      
      # Enable GPU addon with host driver
      echo "Enabling GPU addon with host driver..."
      microk8s enable gpu --driver host
      
      # Wait for pods to start
      echo "Waiting for GPU operator pods to initialize..."
      microk8s kubectl wait --for=condition=Ready pods -n gpu-operator-resources --all --timeout=60s || true
      
      # Check pod status
      echo "Checking pod status..."
      microk8s kubectl get pods -n gpu-operator-resources
      
      # Wait for validation to complete (up to 3 minutes)
      echo "Waiting for validation to complete..."
      VALIDATION_SUCCESS=false
      for i in {1..18}; do
        RESULT=$(microk8s kubectl logs -n gpu-operator-resources -lapp=nvidia-operator-validator -c nvidia-operator-validator 2>/dev/null || echo "waiting")
        if echo "$RESULT" | grep -q "all validations are successful"; then
          echo ""
          echo "=== SUCCESS: GPU addon is ready ==="
          VALIDATION_SUCCESS=true
          break
        fi
        echo -n "."
        sleep 10
      done
      echo ""
      
      if [ "$VALIDATION_SUCCESS" = true ]; then
        echo "GPU support successfully enabled!"
        microk8s kubectl get pods -n gpu-operator-resources
      else
        echo "WARNING: GPU validation did not complete in time. Check pod status manually:"
        echo "microk8s kubectl get pods -n gpu-operator-resources"
        echo "microk8s kubectl logs -n gpu-operator-resources -lapp=nvidia-operator-validator -c nvidia-operator-validator"
      fi
    fi
  else
    echo "Skipping GPU support."
  fi



  microk8s start
  echo "microk8s installation complete!"


  if confirm "Do you want to create local download directories? [y/N]"; then
    echo "Creating local download directories..."
    mkdir -p /data/volumes/sabnzbd-downloads
    chmod 777 -R /data/volumes/sabnzbd-downloads
    mkdir -p /data/volumes/qbittorrent-downloads
    chmod 777 -R /data/volumes/qbittorrent-downloads
    echo "Local download directories created."
  else
    echo "Skipping local download directories creation."
  fi
}



set_kubectl_config() {
  echo "Refreshing certificates..."
  microk8s refresh-certs --cert ca.crt

  echo "Setting up kubeconfig..."
  mkdir -p ~/.kube
  if [ -f ~/.kube/config ]; then
    if confirm "On the remote machine, the file ~/.kube/config already exists. Do you want to replace it? [y/N]"; then
      microk8s config > ~/.kube/config
    else
      echo "Cannot proceed with the installation. Can not replace  ~/.kube/config."
      exit 1
    fi
  else
    microk8s config > ~/.kube/config
  fi


  echo "Waiting for microk8s to be ready..."
  until kubectl wait --for=condition=Ready nodes --all --timeout=600s; do echo "Retrying kubectl wait command..."; sleep 10; done

  echo "copying kubeconfig to current directory"
  cp ~/.kube/config ./kube-config
  chmod 777 ./kube-config

  # schedule task to delete kubeconfig after 3 minutes in the background
   CURRENT_DIR=$(pwd)
   nohup sh -c "(sleep 180 && rm ${CURRENT_DIR}/kube-config)" &


  echo "microk8s setup complete!"
}

ensure_microk8s_installed
set_kubectl_config
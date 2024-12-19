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
  snap install microk8s --classic
  microk8s enable dns
  microk8s enable metallb:$IP_RANGE

  microk8s enable metrics-server
  microk8s enable hostpath-storage

  if confirm "Do you want to enable Nvidia GPU support? [y/N]"; then
   microk8s enable nvidia
  else
    echo "Skipping GPU support."
  fi

  microk8s start
  echo "microk8s installation complete!"
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
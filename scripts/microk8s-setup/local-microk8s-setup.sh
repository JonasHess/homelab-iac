#!/usr/bin/env bash

# Summary: This script automates the setup of a MicroK8s cluster on a remote server.
# It takes the git repo URL, target revision, values path, and a local path for the kube-config file as input.
# It clones the specified repository, reads the values from the specified YAML file,
# connects to the remote server via SSH, installs MicroK8s, enables necessary addons,
# configures MetalLB with the provided IP range, handles optional GPU driver and operator installation,
# retrieves the kube-config file, updates it with the correct cluster, user, and context names,
# and cleans up temporary files on the remote server and local machine.
#
# Usage: ./local-microk8s-setup.sh <repoURL> <targetRevision> <path> <kube-config-path>

# yq is required to parse the values.yaml file.
# To install yq visit https://github.com/mikefarah/yq
#    wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/bin/yq &&\
#    chmod +x /usr/bin/yq

set -e  # Exit on first error
set -o pipefail  # Fail if any command in a pipe fails

cd "$(dirname "$0")"

show_help() {
  echo "Usage: $0 <repoURL> <targetRevision> <path> <kube-config-path> [--help]"
  echo
  echo "Arguments:"
  echo "  <repoURL>          The URL of the git repository (e.g., https://github.com/username/repo.git)"
  echo "  <targetRevision>   The branch or tag to checkout (e.g., main)"
  echo "  <path>             Path to the values.yaml file within the repository (e.g., cluster/values.yaml)"
  echo "  <kube-config-path> The path to the kube-config file (e.g., ~/.kube/config.d/config)"
  echo "  --help             Display this help message."
  exit 0
}

# Check if the help command is provided
if [[ "$1" == "--help" ]]; then
  show_help
fi

# Check for required arguments
if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
  echo "Error: Missing required arguments!"
  show_help
fi

REPO_URL=$1
TARGET_REVISION=$2
VALUES_PATH=$3
KUBE_CONFIG_PATH=$4

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

# Check if the kube-config file already exists
if [ -f "$KUBE_CONFIG_PATH" ]; then
  if confirm "The file $KUBE_CONFIG_PATH already exists. Do you want to replace it? [y/N]"; then
    rm -f "$KUBE_CONFIG_PATH"
  else
    echo "Cannot proceed with the installation. Cannot replace $KUBE_CONFIG_PATH."
    exit 1
  fi
fi

# Create a temporary directory for the git repository
LOCAL_TMP_DIR=$(mktemp -d /tmp/microk8sSetup.XXXXXX)
echo "Created temporary directory: $LOCAL_TMP_DIR"

# Clone the repository and checkout the specified branch/tag
echo "Cloning repository $REPO_URL with branch/tag $TARGET_REVISION..."
git clone --depth 1 --branch "$TARGET_REVISION" "$REPO_URL" "$LOCAL_TMP_DIR"

# Read values from the values.yaml file
VALUES_FILE="$LOCAL_TMP_DIR/$VALUES_PATH"
if [ ! -f "$VALUES_FILE" ]; then
  echo "Error: Values file $VALUES_PATH not found in the repository!"
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Check if yq is installed
if ! command -v yq &> /dev/null; then
  echo "Error: yq is not installed! Please install yq first."
  echo "Visit https://github.com/mikefarah/yq for installation instructions."
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Extract values from the values.yaml file
SERVER_IP=$(yq e '.global.cluster.serverip' "$VALUES_FILE")
USERNAME=$(yq e '.global.cluster.username' "$VALUES_FILE")
IP_RANGE=$(yq e '.global.cluster.ip-range' "$VALUES_FILE")
CLUSTER_NAME=$(yq e '.global.cluster.cluster-name' "$VALUES_FILE")

# Verify that all required values were extracted
if [ -z "$SERVER_IP" ] || [ -z "$USERNAME" ] || [ -z "$IP_RANGE" ] || [ -z "$CLUSTER_NAME" ]; then
  echo "Error: One or more required values not found in $VALUES_FILE!"
  echo "Make sure the file contains all required fields: serverip, username, ip-range, cluster-name"
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Display the extracted values
echo "Extracted configuration values:"
echo "  Server IP: $SERVER_IP"
echo "  Username: $USERNAME"
echo "  IP Range: $IP_RANGE"
echo "  Cluster Name: $CLUSTER_NAME"

# Function to update the kube-config file
update_kube_config() {
  local kube_config_path=$1
  local new_cluster_name=$2
  local new_user_name=$3
  local new_context_name=$4

  echo "Updating kube-config file at $kube_config_path..."

  # Update cluster name
  yq e -i ".clusters[].name = \"$new_cluster_name\"" "$kube_config_path"

  # Update user name
  yq e -i ".users[].name = \"$new_user_name\"" "$kube_config_path"

  # Update context name
  yq e -i ".contexts[].name = \"$new_context_name\"" "$kube_config_path"
  yq e -i ".contexts[].context.cluster = \"$new_cluster_name\"" "$kube_config_path"
  yq e -i ".contexts[].context.user = \"$new_user_name\"" "$kube_config_path"

  # Update current-context
  yq e -i ".\"current-context\" = \"$new_context_name\"" "$kube_config_path"

  echo "kube-config file updated successfully!"
}

echo "Connecting to the remote server via ssh://${USERNAME}@${SERVER_IP}..."
# Create a random subdirectory in /tmp on the remote server
REMOTE_TMP_DIR=$(ssh ${USERNAME}@${SERVER_IP} "mktemp -d /tmp/installMicrok8s.XXXXXX")

echo "Copying script to the remote server into path ${REMOTE_TMP_DIR}..."
scp ./remote-microk8s-setup.sh ${USERNAME}@${SERVER_IP}:${REMOTE_TMP_DIR}/remote-microk8s-setup.sh

echo "Running the remote-microk8s-setup.sh script on the remote server..."
ssh -t ${USERNAME}@${SERVER_IP} "sudo -S bash ${REMOTE_TMP_DIR}/remote-microk8s-setup.sh ${IP_RANGE}"

# Copy the kube-config file to the specified path
echo "Downloading kube-config file to the local machine..."
scp ${USERNAME}@${SERVER_IP}:${REMOTE_TMP_DIR}/kube-config "$KUBE_CONFIG_PATH"
echo "Download complete. The kube-config file is stored at $KUBE_CONFIG_PATH"

# Rename cluster, user and context in kube-config
update_kube_config "$KUBE_CONFIG_PATH" "$CLUSTER_NAME" "$CLUSTER_NAME" "$CLUSTER_NAME"

echo "Setting the kube-config file permissions..."
chmod 600 "$KUBE_CONFIG_PATH"

# Clean up the remote server
echo "Cleaning up the remote server..."
ssh ${USERNAME}@${SERVER_IP} \
  "rm -f ${REMOTE_TMP_DIR}/remote-microk8s-setup.sh ${REMOTE_TMP_DIR}/kube-config && \
  rm ${REMOTE_TMP_DIR}/nohup.out || true && \
  rmdir ${REMOTE_TMP_DIR}" || true

# Clean up the local temporary directory
echo "Cleaning up the local temporary directory..."
rm -rf "$LOCAL_TMP_DIR"

echo "Checking the cluster nodes..."
kubectl --kubeconfig="$KUBE_CONFIG_PATH" get nodes -o wide

echo "$(tput setaf 2)Installation complete!$(tput sgr0)"
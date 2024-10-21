#!/usr/bin/env bash
set -e  # Exit on first error
set -o pipefail  # Fail if any command in a pipe fails

cd "$(dirname "$0")"

list_environments() {
  echo "Available environments:"
  for dir in ../../environments/*/; do
    [ -d "$dir" ] && echo "  - $(basename "$dir")"
  done
}

if [ -z "$1" ]; then
  echo "You are missing the environment argument!"
  echo "Correct usage: $0 <environment> <kube-config-path>"
  list_environments
  exit 1
fi

if [ -z "$2" ]; then
  echo "You are missing the kube-config-path argument (e.g., ~/.kube/config.d/config)!"
  echo "Correct usage: $0 <environment> <kube-config-path>"
  exit 1
fi

ENVIRONMENT=$1
KUBE_CONFIG_PATH=$2
export ENVIRONMENT

# Load parameters from the environment.yaml file
ENV_FILE="../../environments/${ENVIRONMENT}/environment.yaml"
if [ ! -f "$ENV_FILE" ]; then
  echo "Error: Environment file $ENV_FILE not found!"
  exit 1
fi

# Install yq https://github.com/mikefarah/yq
# wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/bin/yq &&\
#    chmod +x /usr/bin/yq
SERVER_IP=$(yq e '.server-ip' "$ENV_FILE")
USERNAME=$(yq e '.username' "$ENV_FILE")
IP_RANGE=$(yq e '.ip-range' "$ENV_FILE")
CLUSTER_NAME=$(yq e '.cluster-name' "$ENV_FILE")

show_help() {
  echo "Usage: $0 <environment> <kube-config-path> [--help]"
  echo
  echo "Arguments:"
  echo "  <environment>       The environment name."
  echo "  <kube-config-path>  The path to the kube-config file (e.g., ~/.kube/config.d/config)."
  echo "  --help              Display this help message."
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

# Check if the help command is provided
if [[ "$1" == "--help" ]]; then
  show_help
fi

# Check if the kube-config file already exists
if [ -f "$KUBE_CONFIG_PATH" ]; then
  if confirm "The file $KUBE_CONFIG_PATH already exists. Do you want to replace it? [y/N]"; then
    rm -f "$KUBE_CONFIG_PATH"
  else
    echo "Cannot proceed with the installation. Cannot replace $KUBE_CONFIG_PATH."
    exit 1
  fi
fi

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
  rm ${REMOTE_TMP_DIR}/nohup.out && \
  rmdir ${REMOTE_TMP_DIR}" || true

echo "Checking the cluster nodes..."
kubectl --kubeconfig="$KUBE_CONFIG_PATH" get nodes -o wide

echo "$(tput setaf 2)Installation complete!$(tput sgr0)"
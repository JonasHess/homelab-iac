#!/usr/bin/env bash

# Summary: This script automates the setup of Argo CD in a Kubernetes cluster.
# It takes git repo URL, target revision, and path to values.yaml as input,
# clones the repository, reads configuration values, switches the kubectl context to the target cluster,
# installs Argo CD, configures the service type (NodePort), waits for the Argo CD server to be ready,
# applies a base configuration, and retrieves the initial admin password.
# It also sets up port-forwarding to the Argo CD server.
#
# Usage: ./setup.sh <repoURL> <targetRevision> <path>

set -e

show_help() {
  echo "Usage: $0 <repoURL> <targetRevision> <path> [--help]"
  echo
  echo "Arguments:"
  echo "  <repoURL>        The URL of the git repository (e.g., https://github.com/username/repo.git)"
  echo "  <targetRevision> The branch or tag to checkout (e.g., main)"
  echo "  <path>           Path to the values.yaml file within the repository (e.g., cluster/values.yaml)"
  echo "  --help           Display this help message."
  exit 0
}

# Check if the help command is provided
if [[ "$1" == "--help" ]]; then
  show_help
fi

# Check for required arguments
if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
  echo "Error: Missing required arguments!"
  show_help
fi

REPO_URL=$1
TARGET_REVISION=$2
VALUES_PATH=$3

# Create a temporary directory for the git repository
LOCAL_TMP_DIR=$(mktemp -d /tmp/argocdSetup.XXXXXX)
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

# Extract cluster name from the values.yaml file
CLUSTER_NAME=$(yq e '.global.cluster.cluster-name' "$VALUES_FILE")


# Verify that required values were extracted
if [ -z "$CLUSTER_NAME" ]; then
  echo "Error: Cluster name not found in $VALUES_FILE!"
  echo "Make sure the file contains the required field: global.cluster.cluster-name"
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Check for required environment variables early in the script
if [ -z "$AKEYLESS_ACCESS_ID" ] || [ -z "$AKEYLESS_ACCESS_TYPE_PARAM" ]; then
  echo "Error: Required environment variables are not set!"
  echo "Please set the following environment variables:"
  echo "  - AKEYLESS_ACCESS_ID"
  echo "  - AKEYLESS_ACCESS_TYPE_PARAM"
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Set default for AKEYLESS_ACCESS_TYPE if missing
if [ -z "$AKEYLESS_ACCESS_TYPE" ]; then
  echo "AKEYLESS_ACCESS_TYPE not set, defaulting to 'api_key'"
  export AKEYLESS_ACCESS_TYPE="api_key"
fi

echo "Found Akeyless credentials in environment variables."

# Display the extracted values
echo "Extracted configuration values:"
echo "  Cluster Name: $CLUSTER_NAME"

# Switch context to the target environment
echo "Switching kubectl context to $CLUSTER_NAME..."
kubectl config use-context "$CLUSTER_NAME"

# Create the argocd namespace if it does not exist
echo "Creating argocd namespace if it doesn't exist..."
kubectl create namespace argocd 2>/dev/null || true

# Create and apply the Akeyless secret
echo "Creating the Akeyless secret..."
cat << EOF | envsubst | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: akeyless-secret-creds
  namespace: argocd
type: Opaque
stringData:
  accessId: "${AKEYLESS_ACCESS_ID}"
  accessType: "${AKEYLESS_ACCESS_TYPE}"
  accessTypeParam: "${AKEYLESS_ACCESS_TYPE_PARAM}"
EOF

# Install ArgoCD using Helm
echo "Adding ArgoCD Helm repository if it doesn't exist..."
helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
echo "Updating Helm repositories..."
helm repo update

# Check if ArgoCD is already installed in the cluster
if helm list -n argocd | grep -q "argocd"; then
  echo "ArgoCD is already installed. Upgrading installation..."
  helm upgrade argocd argo/argo-cd --namespace argocd
else
  echo "Installing ArgoCD using Helm..."
  helm install argocd argo/argo-cd --namespace argocd
fi

# use nodeport to expose the service
echo "Configuring argocd-server service as NodePort..."
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'

echo "Waiting for argocd-server pod to be ready..."
while true; do
  if kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s > /dev/null 2>&1; then
    echo "argocd-server is now ready"
    break
  fi
  sleep 1
  echo -n "."
done

# Install or upgrade the bootstrap chart
echo "Installing/upgrading the bootstrap chart..."

# Check if the bootstrap chart is already installed
if helm list -n argocd | grep -q "bootstrap"; then
  echo "Bootstrap chart is already installed. Upgrading..."
  helm upgrade bootstrap ../bootstrap-chart \
    --namespace argocd \
    --set basechart.values.repoURL="$REPO_URL" \
    --set basechart.values.targetRevision="$TARGET_REVISION" \
    --set basechart.values.path="$VALUES_PATH"
else
  echo "Installing bootstrap chart..."
  helm install bootstrap ../bootstrap-chart \
    --namespace argocd \
    --set basechart.values.repoURL="$REPO_URL" \
    --set basechart.values.targetRevision="$TARGET_REVISION" \
    --set basechart.values.path="$VALUES_PATH"
fi

# Get the initial admin password
echo "Initial admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

# Clean up the local temporary directory
echo "Cleaning up the local temporary directory..."
rm -rf "$LOCAL_TMP_DIR"

echo "Argo CD setup complete!"

echo ">>>>>>>>>  DONT FORGET TO  RESTART ARGO  ONCE <<<<<<<<<<"

# port-forward to the service
echo "Port-forwarding to the argocd-server service..."
kubectl port-forward svc/argocd-server -n argocd 8081:443

exit 0
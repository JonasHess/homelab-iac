#!/usr/bin/env bash

# Summary: This script automates the setup of Argo CD in a Kubernetes cluster.
# It takes git repo URL, target revision, and path to values.yaml as input,
# clones the repository, reads configuration values, installs or upgrades Argo CD (exposed as NodePort)
# in the target cluster, waits for the Argo CD server to be ready, applies a base configuration,
# and retrieves the initial admin password. It also sets up port-forwarding to the Argo CD server.
# Re-running it on an existing cluster upgrades Argo CD to ARGOCD_CHART_VERSION.
# Every kubectl/helm call targets <kube-context> explicitly; the current context is never changed.
#
# Usage: ./setup.sh <repoURL> <targetRevision> <path> [<kube-context>]

set -e

cd "$(dirname "$0")"

# argo-cd Helm chart version (https://github.com/argoproj/argo-helm). Raise it to upgrade Argo CD;
# read the chart's and Argo CD's upgrade notes for every version skipped in between.
# renovate: datasource=helm depName=argo-cd registryUrl=https://argoproj.github.io/argo-helm
ARGOCD_CHART_VERSION="10.11.6"

# Exits if the cluster already runs a newer argo-cd chart than ARGOCD_CHART_VERSION:
# "helm upgrade --version" would install the older chart and its older CRDs over it.
# ALLOW_ARGOCD_DOWNGRADE=1 skips the check for a deliberate downgrade.
check_no_argocd_downgrade() {
  local installed
  installed=$(helm list --kube-context "$1" -n argocd -o json 2>/dev/null \
    | python3 -c 'import json,sys; print(next((r["chart"].removeprefix("argo-cd-") for r in json.load(sys.stdin) if r["name"] == "argocd"), ""))')
  if [ -z "$installed" ] || [ "$installed" = "$2" ] || [ "${ALLOW_ARGOCD_DOWNGRADE:-0}" = "1" ]; then
    return 0
  fi
  if [ "$(printf '%s\n%s\n' "$installed" "$2" | sort -V | tail -n 1)" = "$installed" ]; then
    echo "Error: the cluster runs argo-cd chart $installed, newer than ARGOCD_CHART_VERSION $2."
    echo "Raise ARGOCD_CHART_VERSION, or set ALLOW_ARGOCD_DOWNGRADE=1 to downgrade on purpose."
    exit 1
  fi
}

show_help() {
  echo "Usage: $0 <repoURL> <targetRevision> <path> [<kube-context>] [--help]"
  echo
  echo "Arguments:"
  echo "  <repoURL>        The URL of the git repository (e.g., https://github.com/username/repo.git)"
  echo "  <targetRevision> The branch or tag to checkout (e.g., main)"
  echo "  <path>           Path to the values.yaml file within the repository (e.g., cluster/values.yaml)"
  echo "  <kube-context>   The kubectl context of the target cluster (default: global.cluster.cluster-name)"
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
KUBE_CONTEXT=$4

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
# '// ""': yq prints the word null for a missing key, which -z below would accept.
CLUSTER_NAME=$(yq e '.global.cluster.cluster-name // ""' "$VALUES_FILE")


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

KUBE_CONTEXT=${KUBE_CONTEXT:-$CLUSTER_NAME}
if ! kubectl config get-contexts "$KUBE_CONTEXT" > /dev/null 2>&1; then
  echo "Error: kubectl context '$KUBE_CONTEXT' not found!"
  echo "Pass the context of the target cluster as the 4th argument (see: kubectl config get-contexts)."
  rm -rf "$LOCAL_TMP_DIR"
  exit 1
fi

# Display the extracted values
echo "Extracted configuration values:"
echo "  Cluster Name: $CLUSTER_NAME"
echo "  Kube Context: $KUBE_CONTEXT"
echo "  Argo CD Chart Version: $ARGOCD_CHART_VERSION"

# Create the argocd namespace if it does not exist
echo "Creating argocd namespace if it doesn't exist..."
kubectl --context "$KUBE_CONTEXT" create namespace argocd 2>/dev/null || true

# Create and apply the Akeyless secret
echo "Creating the Akeyless secret..."
cat << EOF | envsubst | kubectl --context "$KUBE_CONTEXT" apply -f -
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

# Install or upgrade ArgoCD using Helm
echo "Adding ArgoCD Helm repository if it doesn't exist..."
helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
echo "Updating the ArgoCD Helm repository..."
helm repo update argo

check_no_argocd_downgrade "$KUBE_CONTEXT" "$ARGOCD_CHART_VERSION"
echo "Installing/upgrading ArgoCD to chart version $ARGOCD_CHART_VERSION..."
helm upgrade --install argocd argo/argo-cd \
  --kube-context "$KUBE_CONTEXT" \
  --namespace argocd \
  --version "$ARGOCD_CHART_VERSION" \
  --set server.service.type=NodePort

echo "Waiting for argocd-server pod to be ready..."
while true; do
  if kubectl --context "$KUBE_CONTEXT" wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s > /dev/null 2>&1; then
    echo "argocd-server is now ready"
    break
  fi
  sleep 1
  echo -n "."
done

echo "Installing/upgrading the bootstrap chart..."
helm upgrade --install bootstrap ../bootstrap-chart \
  --kube-context "$KUBE_CONTEXT" \
  --namespace argocd \
  --set basechart.values.repoURL="$REPO_URL" \
  --set basechart.values.targetRevision="$TARGET_REVISION" \
  --set basechart.values.path="$VALUES_PATH"

# Argo CD only creates this secret on first install, and it is commonly deleted after the first login
if kubectl --context "$KUBE_CONTEXT" -n argocd get secret argocd-initial-admin-secret > /dev/null 2>&1; then
  echo "Initial admin password:"
  kubectl --context "$KUBE_CONTEXT" -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
fi

# Clean up the local temporary directory
echo "Cleaning up the local temporary directory..."
rm -rf "$LOCAL_TMP_DIR"

echo "Argo CD setup complete!"

echo ">>>>>>>>>  DONT FORGET TO  RESTART ARGO  ONCE <<<<<<<<<<"

# port-forward to the service
echo "Port-forwarding to the argocd-server service..."
kubectl --context "$KUBE_CONTEXT" port-forward svc/argocd-server -n argocd 8081:443

exit 0
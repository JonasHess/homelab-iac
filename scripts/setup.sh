#!/usr/bin/env bash
set -e


# Function to list all available environment directories
list_environments() {
  echo "Available environments:"
  for dir in ../environments/*/; do
    [ -d "$dir" ] && echo "  - $(basename "$dir")"
  done
}

if [ -z "$1" ]; then
  echo "You are missing the environment argument!"
  echo "Correct usage: $0 <environment>"
  list_environments
  exit 1
fi


ENVIRONMENT=$1
export ENVIRONMENT

# Load parameters from the environment.yaml file
ENV_FILE="../environments/${ENVIRONMENT}/environment.yaml"
if [ ! -f "$ENV_FILE" ]; then
  echo "Error: Environment file $ENV_FILE not found!"
  exit 1
fi

CLUSTER_NAME=$(yq e '.cluster-name' "$ENV_FILE")



ENV_SECRET_FILE="../environments/${ENVIRONMENT}/environment-secrets.yaml"
if [ ! -f "$ENV_SECRET_FILE" ]; then
  echo "Error: Environment file $ENV_SECRET_FILE not found!"
  exit 1
fi

# Load the environment secrets
AKEYLESS_ACCESS_ID=$(yq e '.akeyless.accessId' "$ENV_SECRET_FILE")
AKEYLESS_ACCESS_TYPE=$(yq e '.akeyless.accessType' "$ENV_SECRET_FILE")
AKEYLESS_ACCESS_TYPE_PARAM=$(yq e '.akeyless.accessTypeParam' "$ENV_SECRET_FILE")

export AKEYLESS_ACCESS_ID
export AKEYLESS_ACCESS_TYPE
export AKEYLESS_ACCESS_TYPE_PARAM


# Switch context to the target environment
kubectl config use-context "$CLUSTER_NAME"


# Create the argocd namespace if it does not exist
kubectl create namespace argocd 2>/dev/null || true


# Install Argocd on the cluster

#kubectl create namespace argocd
kubectl apply -k https://github.com/argoproj/argo-cd/manifests/crds\?ref\=stable
#kubectl create namespace argocd || true
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# use LoadBalancer to expose the service
# kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
#kubectl apply -n argocd -f argocd-nodeport.yaml

# use nodeport to expose the service
echo "Using NodePort to expose the argocd-server service..."
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'


echo "Waiting for argocd-server pod to be created..."
while true; do
  if kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s > /dev/null 2>&1; then
    echo "argocd-server is now ready"
    break
  fi
  sleep 1
  echo -n "."
done


# Apply the base.yaml with the environment variable substituted
envsubst < ../environments/base.yaml | kubectl apply -f -

kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo


echo "Argocd setup complete!"


# port-forward to the service
echo "Port-forwarding to the argocd-server service..."
kubectl port-forward svc/argocd-server -n argocd 8081:443

exit 0
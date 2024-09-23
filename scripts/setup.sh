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

# Create the argocd namespace if it does not exist
kubectl create namespace argocd 2>/dev/null || true


# Check if the akeyless-apikey.yaml file exists
if [ ! -f ./secrets/akeyless-apikey.yaml ]; then
  echo "Error: ./secrets/akeyless-apikey.yaml file not found!"
  exit 1
fi

kubectl apply -f ./secrets/akeyless-apikey.yaml


# Install Argocd on the cluster

#kubectl create namespace argocd
kubectl apply -k https://github.com/argoproj/argo-cd/manifests/crds\?ref\=stable
kubectl create namespace argocd || true
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# use LoadBalancer to expose the service
# kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
#kubectl apply -n argocd -f argocd-nodeport.yaml

# use nodeport to expose the service
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'


# wait for the argocd-server to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s


# Apply the base.yaml with the environment variable substituted
envsubst < ./base.yaml | kubectl apply -f -

kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo


echo "Argocd setup complete!"


# port-forward to the service
echo "Port-forwarding to the argocd-server service..."
kubectl port-forward svc/argocd-server -n argocd 8080:443 &

exit 0
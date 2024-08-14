#!/bin/sh
set -e

export GITHUB_USER=JonasHess
export GITHUB_REPO=homelab-iac
#export GITHUB_TOKEN=$(cat env/github-token-secret.txt)
export GIT_BRANCH="main"
KIND_CLUSTER_NAME="homelab"


# Parse command-line arguments
cluster="local"
skip_cluster_create=false

while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -c|--cluster)
            cluster="$2"
            shift
            shift
            ;;
        -s|--skip-cluster-create)
            skip_cluster_create=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

#echo "Validating manifests"
#sh ./scripts/validate.sh


if [ "$skip_cluster_create" = false ]; then
  # Delete existing Kind cluster if it exists
  if kind get clusters | grep -q "^$KIND_CLUSTER_NAME$"; then
      echo "Deleting existing Kind cluster..."
      kind delete cluster --name $KIND_CLUSTER_NAME
  fi

  # Create Kind cluster
  echo "Creating Kind cluster..."
  kind create cluster --name $KIND_CLUSTER_NAME
else
    echo "Skipping cluster creation as requested..."
fi


# Wait for cluster to be ready
echo "Waiting for Kind cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=300s

# Show cluster info
kubectl cluster-info --context kind-$KIND_CLUSTER_NAME

echo "Cluster Setup complete!"
















#
## Check Kubernetes version is compatible
#flux check --pre
#
## Bootstrap the cluster
#flux bootstrap github \
#    --owner=${GITHUB_USER} \
#    --repository=${GITHUB_REPO} \
#    --branch=${GIT_BRANCH} \
#    --personal \
#    --path="clusters/$cluster"
#
#echo "Give the pods some time to be ready..."
#sleep 5  # Sleep interval in seconds
#
#
#
#
##GRAFANA_PASSWORD=$(kubectl get secret --namespace $NAMESPACE loki-grafana -o jsonpath="{.data.admin-password}" | base64 --decode)
#echo "Grafana and Wave password is: flux"
#
## This requires a dns rewrite for *.coding-challenge.hess.lan to localhost
#echo "Available ingress:"
#echo "http://weave.coding-challenge.hess.lan/"
#echo "http://kuard.coding-challenge.hess.lan"
#echo "http://grafana.coding-challenge.hess.lan"
#echo "http://prometheus.coding-challenge.hess.lan"
##echo "http://alertmanager.coding-challenge.hess.lan"
#
#
#echo "Forward traffic on port 80 to ingress controller..."
#sudo kubectl port-forward service/ingress-traefik -n traefik 80:80
#
## Grafana port-forward
##kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana  3000:80
#
#echo "Done."
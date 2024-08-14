#!/usr/bin/env bash
set -e


# Install Argocd on the cluster

#kubectl create namespace argocd
kubectl apply -k https://github.com/argoproj/argo-cd/manifests/crds\?ref\=stable
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# use LoadBalancer to expose the service
#kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'





# wait for the argocd-server to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s

#kubectl apply -n argocd -f argocd-nodeport.yaml

kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

# apply application.yaml
kubectl apply -f ../manifests/homelab-base-chart.yaml


# port-forward to the service
kubectl port-forward svc/argocd-server -n argocd 8080:443 &

## ARGOCD

## Add Git Repo to ArgoCD

## Argo will deploy the homelab-base-chart

exit 0
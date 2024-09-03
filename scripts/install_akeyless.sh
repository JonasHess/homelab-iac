#!/usr/bin/env bash
set -e


kubectl create namespace argocd || true
kubectl apply -f ./secrets/akeyless-apikey.yaml
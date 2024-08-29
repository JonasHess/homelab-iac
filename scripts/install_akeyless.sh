#!/usr/bin/env bash
set -e


kubectl create namespace akeyless || true
kubectl apply -f ./secrets/akeyless-apikey.yaml
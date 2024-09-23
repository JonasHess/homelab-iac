#!/bin/bash
set -e

go install sigs.k8s.io/cloud-provider-kind@latest
sudo install ~/go/bin/cloud-provider-kind /usr/local/bin

cloud-provider-kind
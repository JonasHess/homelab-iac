#!/usr/bin/env bash
#
# The job scripts ship inside ConfigMaps, so what runs in the cluster is whatever Helm
# renders - not the file on disk. This checks the rendered form: that the chart renders
# at all, that every script actually landed in its ConfigMap, and that what landed is
# valid Python.
#
# The "actually landed" part is the point. An extraction that silently yields nothing
# makes every downstream check pass on an empty file, which is exactly how a broken
# script would reach ArgoCD looking green.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/.rendered"
rm -rf "$out"; mkdir -p "$out"

helm template firefly-importer "$repo/apps/firefly-importer" \
  --namespace argocd \
  --values "$repo/tests/alert-rules/values.yaml" \
  --values "$here/values.yaml" \
  > "$out/firefly-importer.yaml"

python3 "$here/extract-scripts.py" "$out/firefly-importer.yaml" "$out"

for f in "$out"/*.py; do
  python3 -m py_compile "$f"
  echo "ok  $(basename "$f")  $(wc -l < "$f" | tr -d ' ') lines"
done

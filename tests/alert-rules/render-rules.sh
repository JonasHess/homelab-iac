#!/usr/bin/env bash
#
# Renders every chart that ships a PrometheusRule and writes each rule group set to
# .rendered/<chart>.yaml in Prometheus' own rule-file format, which is exactly the
# PrometheusRule's .spec. promtool consumes those; the .test.yaml files next to this
# script reference them.
#
# Rendering rather than testing the YAML directly is the point: the rules are Helm
# templates, so the thing worth testing is what Helm actually produces.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/.rendered"
rm -rf "$out"; mkdir -p "$out"

# Charts that ship alert rules. Add to this list when a new chart gets a PrometheusRule.
# Extra args are the values each chart needs to render at all - unrelated to alerting,
# but a chart that will not render cannot be tested.
render() {
  local chart="$1"; shift
  helm template "$chart" "$repo/apps/$chart" \
    --namespace argocd \
    --values "$here/values.yaml" \
    "$@" \
  | python3 "$here/extract-rules.py" "$out/$chart.yaml" "$chart"
}

render prometheus \
  --set generic.appName=prometheus \
  --set generic.persistentVolume.prometheus=/tmp/p \
  --set generic.persistentVolume.alertmanager=/tmp/a \
  --set generic.persistentVolumeClaims.grafana.hostPath=/tmp/g

render envoy-gateway --set generic.appName=envoy-gateway

render cert-manager --set generic.appName=cert-manager --set certManager.version=v1.17.2

render restic --set appName=restic --set generic.appName=restic \
  --set generic.persistentVolumeClaims.tank1.hostPath=/tmp/tank1 \
  --set generic.persistentVolumeClaims.restoreddata.hostPath=/tmp/restored

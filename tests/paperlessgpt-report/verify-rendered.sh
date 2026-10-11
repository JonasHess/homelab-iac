#!/usr/bin/env bash
#
# The report script ships inside a ConfigMap, so what runs is whatever Helm renders.
# This checks that the job only exists when enabled, that the script landed in the
# ConfigMap byte for byte, and that the theme list reaches the job as valid JSON.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/.rendered"
rm -rf "$out"; mkdir -p "$out"

render() {
  helm template paperlessgpt "$repo/apps/paperlessgpt" \
    --namespace argocd \
    --values "$repo/tests/alert-rules/values.yaml" \
    --values "$repo/tests/paperlessgpt-prompt/values.yaml" \
    "$@"
}

render > "$out/disabled.yaml"
render --values "$here/values.yaml" > "$out/enabled.yaml"
python3 "$here/check-rendered.py" "$out" "$repo/apps/paperlessgpt/scripts/quality_report.py"

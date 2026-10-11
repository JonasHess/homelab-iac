#!/usr/bin/env bash
#
# paperless-gpt reads its JSON prompt from a ConfigMap that Helm assembles from
# apps/paperlessgpt/json_prompt*.tmpl. The n8n-only output fields (summary, urgency,
# action_*) must be in the prompt exactly when N8N_WEBHOOK_URL is set: without a webhook
# nobody reads them, and with one the n8n workflow depends on them.
#
# The prompt is itself a Go template that paperless-gpt renders per document, so its
# {{ ... }} placeholders must reach the ConfigMap untouched - Helm must not evaluate them.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/.rendered"
rm -rf "$out"; mkdir -p "$out"

render() {
  helm template paperlessgpt "$repo/apps/paperlessgpt" \
    --namespace argocd \
    --values "$repo/tests/alert-rules/values.yaml" \
    --values "$here/values.yaml" \
    "$@"
}

render > "$out/without-n8n.yaml"
render --set N8N_WEBHOOK_URL=https://n8n.test.example/webhook/paperless-gpt > "$out/with-n8n.yaml"

python3 "$here/check-prompt.py" "$out/without-n8n.yaml" without-n8n
python3 "$here/check-prompt.py" "$out/with-n8n.yaml" with-n8n

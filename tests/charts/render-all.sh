#!/usr/bin/env bash
#
# Renders every app chart the way ArgoCD would and writes each result to
# .rendered/<app>.yaml. Fails if any chart does not render, or if a chart's
# vendored charts/*.tgz do not match its Chart.lock: ArgoCD renders the vendored
# archives, so a Chart.lock bump without them deploys the old dependency.
#
# The environment overlays are not in this repo, so the globals come from
# tests/alert-rules/values.yaml, every empty hostPath gets a placeholder
# (fill-host-paths.py), and the few values an app requires per instance come from
# values/<app>.yaml.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/.rendered"
rm -rf "$out"; mkdir -p "$out"

failed=0
for dir in "$repo"/apps/*/; do
  app="$(basename "$dir")"
  [ -f "$dir/Chart.yaml" ] || continue
  # A library chart; it is rendered through every app that depends on it.
  [ "$app" = generic ] && continue

  if [ -f "$dir/Chart.lock" ]; then
    while read -r name version; do
      if [ ! -f "$dir/charts/$name-$version.tgz" ]; then
        echo "FAIL $app: Chart.lock wants $name $version but charts/$name-$version.tgz is missing" >&2
        failed=1
      fi
    done < <(yq '.dependencies[] | .name + " " + .version' "$dir/Chart.lock")
  fi

  extra=()
  [ -f "$here/values/$app.yaml" ] && extra=(--values "$here/values/$app.yaml")
  python3 "$here/fill-host-paths.py" "$dir/values.yaml" > "$out/$app.paths.json"

  if ! helm template "$app" "$dir" \
      --namespace argocd \
      --values "$repo/tests/alert-rules/values.yaml" \
      --values "$out/$app.paths.json" \
      "${extra[@]}" \
      --set appName="$app" --set generic.appName="$app" \
      > "$out/$app.yaml" 2> "$out/$app.err"; then
    echo "FAIL $app: $(grep -v '^level=' "$out/$app.err" | head -5)" >&2
    failed=1
  fi
  rm -f "$out/$app.paths.json"
done

exit "$failed"

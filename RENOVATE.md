# Renovate Configuration

This repository uses Renovate to automatically update Helm chart versions in ArgoCD Application templates.

## How It Works

Renovate scans all files matching `apps/*/templates/*-application.yaml` and looks for patterns like:

```yaml
source:
  chart: <chart-name>
  repoURL: <helm-repo-url>
  targetRevision: <version>
```

## Automation Rules

- **Patch & Minor Updates**: Auto-merged automatically
- **Major Updates**: Require manual approval (labeled with `major-update`)
- **Individual PRs**: One PR per chart update
- **Schedule**: Weekday early mornings and weekends

## How to Exclude Charts

To exclude specific charts from updates, add them to the `ignoreDeps` array in `.renovaterc.json`:

```json
"ignoreDeps": [
  "kube-prometheus-stack",
  "traefik"
]
```

## How to Change Update Policies

You can add per-chart rules in the `packageRules` section. For example, to make a specific chart always require manual approval:

```json
{
  "description": "Always require manual approval for critical chart",
  "matchCategories": ["helm"],
  "matchPackageNames": ["kube-prometheus-stack"],
  "automerge": false,
  "addLabels": ["critical-chart"]
}
```
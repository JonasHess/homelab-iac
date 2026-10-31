# Renovate Configuration

Renovate keeps Helm charts, container images, Python packages and pinned tool versions up to date. The configuration is `.renovaterc.json`; this file explains it.

## What Renovate finds

Most dependencies here are found by the regex `customManagers`, not by Renovate's built-in managers:

| Where | What | Manager |
|---|---|---|
| `apps/*/templates/*-application.yaml` | `chart` + `repoURL` + `targetRevision` of upstream Helm charts | regex, datasource `helm` |
| `apps/**/*.yaml` | `image: repo:tag` and `repository:` + `tag:` pairs | regex, datasource `docker` |
| `apps/**/*.yaml` | pip pins in lists (`- name==1.2.3`), `pytrVersion` | regex, datasource `pypi` |
| `Dockerfile`s, `apps/**/*.yaml`, `scripts/*.sh`, `.github/workflows/*.yml` | any value with a `# renovate: datasource=... depName=...` comment on the line above | regex |
| `apps/**/*.yaml` | git commit pins with `# renovate: datasource=git-refs depName=<repo> branch=<branch>` | regex, datasource `git-refs` |
| `.github/workflows/*.yml` | `uses:` actions | built-in `github-actions` |

To track a new pinned version, add the annotation comment directly above it, e.g.:

```yaml
# renovate: datasource=docker depName=python
python-version: '3.15'
```

The same `depName` in several files lands in one PR (e.g. every `python` pin), so related pins move together.

## Automation rules

- **Patch and minor updates** auto-merge once every CI check passes (Charts, Alert Rules, App Scripts, ...). A red check keeps the PR open, so a bad bump never reaches ArgoCD.
- **Major updates** need manual approval and get the `major-update` label.
- **Never auto-merged**, whatever the update type: `envoyproxy/gateway-helm` and `cert-manager` (label `manual-review`). They carry every route and every certificate, so even a minor release can break all apps at once.
- **Grouped**: all `immich-app/immich-*` images in one PR, because server and machine-learning must run the same version.
- **Disabled**: private ECR images (`<account>.dkr.ecr.<region>.amazonaws.com/...`). Renovate has no ECR credentials; the app's own image workflow opens the deploy PR (e.g. `.github/workflows/restic-image.yml`).
- **Custom versioning** for qBittorrent, Jellyfin and OpenCCU, whose tags are not plain semver, and for `ghcr.io/immich-app/postgres` (`<pg-major>-vectorchord<x.y.z>`): the Postgres major is a separate, manual PR (dump/restore), vectorchord bumps come as manual minor/patch PRs.
- **Matching**: rules select by datasource (`docker`, `helm`, `pypi`, `npm`, `go`, `github-releases`), not by category. Categories only exist for Renovate's built-in managers, so a category rule would silently never apply to the regex-found dependencies. GitHub Actions still match category `ci`, which works because the built-in manager finds them.
- **Rebasing**: `rebaseWhen: conflicted`. With automerge on, Renovate would otherwise rebase every branch that falls behind `main`, which restarts its checks and limits it to one merge per run.
- **Schedule**: daily between 12:00 and 22:00 (Europe/Berlin).

## How to exclude a dependency

Add a `packageRules` entry that disables it, with the reason in `description`:

```json
{
  "description": "Pinned on purpose: <why>",
  "matchPackageNames": ["kube-prometheus-stack"],
  "enabled": false
}
```

## How to change the policy for one dependency

Add a `packageRules` entry matched by package name; later rules override earlier ones:

```json
{
  "description": "Always review <name> by hand: <why>",
  "matchPackageNames": ["kube-prometheus-stack"],
  "automerge": false,
  "addLabels": ["manual-review"]
}
```

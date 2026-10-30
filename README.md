# Infrastructure as Code for Home Lab

Kubernetes/Helm-based Infrastructure as Code for a self-hosted home lab, managed declaratively with GitOps via ArgoCD. Push to the repo and the cluster reconciles itself.

## Architecture

- **GitOps with ArgoCD** — every application is an ArgoCD `Application` rendered from this repository. Changes merged to `main` are picked up and reconciled automatically.
- **Generic chart pattern** — most apps in `apps/` are thin wrappers around a single shared Helm chart at `apps/generic/`. App charts only declare the values they need (image, ports, ingress, mounts, secrets, backup); the generic chart renders the Deployment, Service, Ingress, PVCs, ExternalSecrets, and Restic backup CRDs. Validated via `values.schema.json`.
- **Application registry** — `base-chart/values.yaml` is the central registry: which apps are enabled, their Homer dashboard metadata, ArgoCD project/namespace, and `syncWave` ordering. The `base-chart` itself is an ArgoCD app-of-apps that fans out to every enabled application.
- **Bootstrap** — `bootstrap-chart/` performs the initial ArgoCD install and registers `base-chart` so the rest of the cluster comes up under GitOps from there.
- **Secrets** — Akeyless is the source of truth; the External Secrets Operator pulls secrets into Kubernetes on demand. No secrets are stored in this repo.
- **Environment overrides** — environment-specific values (e.g. `zimmermann.lat`) live in a separate `homelab-environments` repo and are layered on top of `base-chart` defaults.

## Repository Layout

```
apps/                    # Per-application Helm charts (one folder per app)
  generic/               # Shared base chart consumed by most apps
  bump_generic_chart.sh  # Bumps the generic chart version everywhere it's referenced
base-chart/              # App registry + ArgoCD app-of-apps
bootstrap-chart/         # Initial ArgoCD bootstrap
scripts/
  setup.sh               # Bootstrap entrypoint
  helm-tools/            # create_app.py — interactive new-app scaffolder
  microk8s-setup/        # MicroK8s setup helpers
  zfs-exporter/          # Prometheus ZFS exporter installer
doc/                     # Operational notes (Cognito, Homematic, ZFS, Paperless, ...)
docs/                    # Architecture docs: gateway + OIDC, backup and recovery, Envoy migration
tests/                   # CI checks: chart render + kubeconform, alert rules, job scripts, restic
```

## Core Technologies

- **Kubernetes** — container orchestration runtime
- **Helm** — packaging and templating for all applications
- **ArgoCD** — declarative GitOps continuous delivery
- **Akeyless** + **External Secrets Operator** — secrets management
- **Envoy Gateway** + **Gateway API** — ingress controller (HTTPS / TCP / UDP via `HTTPRoute` / `TCPRoute` / `UDPRoute`; native OIDC `SecurityPolicy` against AWS Cognito; mTLS-gated Cloudflare-only listener pair on `:4443`)
- **Crossplane** + **AWS controllers** — Kubernetes-managed cloud resources
- **Prometheus** / **Grafana** / **Alertmanager** — monitoring stack
- **Restic** + **Backrest** — backup orchestration
- **Renovate** — automated dependency updates
- **Cloudflare** — DNS and DDNS
- **ZFS** — underlying storage with compression and snapshots

## Getting Started

### Bootstrap a cluster
```bash
# Installs ArgoCD and registers the base-chart for one environment.
# kube-context defaults to global.cluster.cluster-name from the env values.
./scripts/setup.sh <repoURL> <targetRevision> <path-to-env-values.yaml> [<kube-context>]
```

### Add a new application
```bash
python scripts/helm-tools/create_app.py   # Interactive scaffolder
```
Then register it in `base-chart/values.yaml` under `apps.<name>` (`enabled: false`, `homer`, `argocd`, `syncWave`) and switch it on in the environment repo (`apps.<name>.enabled: true` plus its `hostPath` values). See `CLAUDE.md` for the full template.

### Bump the generic chart
Run **only** when you change something inside `apps/generic/`:
```bash
cd apps && ./bump_generic_chart.sh
```
This bumps the version and updates every dependent app chart. Editing an app's own `values.yaml` or `templates/` does **not** require this — ArgoCD picks those up on push.

## Deployed Applications & Services

Apps are organised by their dashboard group in `base-chart/values.yaml`.

### Media & Entertainment
- **Plex** / **Jellyfin** — media servers
- **Tautulli** — Plex monitoring
- **Overseerr** — request management and discovery
- **Audiobookshelf** — audiobook and podcast server
- **Immich** — self-hosted photo and video backup
- **SFTPGo** — SFTP/FTP server
- **TeddyCloud** - Toniebox cloud replacement
- **tonie-studio** - builds travel tonies from the audio archive

### *arr Stack
- **Radarr** — movies
- **Sonarr** — TV
- **Readarr** — ebooks
- **Prowlarr** — indexer manager
- **Profilarr** — *arr profile management

### Downloads
- **qBittorrent** — torrent client
- **SABnzbd** / **NZBGet** — Usenet downloaders

### Smart Home
- **Home Assistant** — automation platform
- **HomeMatic** — automation controller
- **Zigbee2MQTT** — Zigbee → MQTT gateway
- **Mosquitto** — MQTT broker
- **smarthome4** — custom smart-home service
- **smarthome4-ui** — FastAPI + React UI for Zigbee2MQTT scenes
- **alexa-custom-skill** / **alexa-smarthome-skill** — Alexa integrations
- **zigbee-light-watcher** - exports lights whose real on/off state disagrees with Home Assistant as metrics

### Productivity
- **Paperless-ngx** + **Paperless-GPT** — document management with LLM classification
- **ASN** — small redirector to Paperless via archive serial numbers
- **Gotenberg** / **Tika** — document conversion and content analysis
- **Stirling-PDF** — local PDF manipulation
- **Vaultwarden** — self-hosted Bitwarden
- **Mealie** — recipe manager
- **Radicale** — CalDAV / CardDAV server
- **Nextcloud** — file sync and collaboration
- **FreshRSS** — RSS aggregator
- **n8n** — workflow automation
- **portal-document-downloader** / **pytr** / **justtrade** / **c24-document-downloader** - automated document fetchers
- **Firefly III** + **firefly-importer** + **firefly-sankey** - personal finance, bank import, Sankey money-flow diagrams
- **InfCloud** - CalDAV/CardDAV web client
- **Kalibra** - scale and weight calibration records
- **facilioo-watch** - report on stalled property-management processes
- **venice-autobook** - auto-books capacity-limited gym classes

### AI & ML
- **Ollama** — local LLM runtime
- **OpenWebUI** — chat UI for Ollama
- **prompt-util** — prompt utility service

### Infrastructure
- **ArgoCD** — GitOps controller
- **Envoy Gateway** — ingress / reverse proxy via Gateway API
- **Akeyless** + **External Secrets Operator** — secrets
- **Reloader** — restarts pods on ConfigMap/Secret changes
- **Crossplane** + **AWS controllers** — cloud resource management
- **AdGuard** — DNS-based ad blocking
- **Cloudflare DDNS** — dynamic DNS updates
- **Postgres** / **Redis** — shared datastores
- **Homer** — dashboard
- **Backrest** / **Restic** — backups
- **filecleanup** — scheduled cleanup of old files / empty dirs
- **samba** — file sharing
- **Whoami** — debug HTTP service

### Monitoring & Observability
- **Prometheus** — metrics
- **Beszel** - lightweight server monitoring
- **Grafana** — dashboards
- **Alertmanager** — alert routing
- **Duplicati Prometheus Exporter** — backup metrics
- **TGTG** — TooGoodToGo notifications

## Dependency Management

Renovate keeps Helm chart versions, Docker images, and language packages up to date. Highlights:

- Patch and minor updates auto-merge once every CI check passes; major updates require manual approval.
- Never auto-merged, whatever the update type: Envoy Gateway and cert-manager (a broken minor release takes down every route or certificate at once).
- Private ECR images are invisible to Renovate; the app's own image workflow opens the deploy PR instead (e.g. `restic-image.yml`).
- Custom regex managers detect ArgoCD `targetRevision` and inline image references.
- Configuration: `.renovaterc.json`. See [`RENOVATE.md`](RENOVATE.md) for usage and how to exclude charts.

## TODOs & Future Improvements

- [ ] **Restart Pod argocd-server** — automate restart to handle Akeyless secret race conditions
- [ ] **Per-app namespaces** — migrate apps off the shared `argocd` namespace. *Plumbing complete (May 2026)*: every app can opt in via `apps.<app>.argocd.namespace`; the generic chart auto-emits cross-namespace `ReferenceGrant`s for HTTPRoutes (immich, envoy-gateway, cert-manager, homeassistant, trade-republic and zigbee-light-watcher have their own namespaces today). Mass migration of the remaining ~25 apps still outstanding; data-layer apps (postgres/redis/mosquitto) need FQDN connection strings in consumers when moved.
- [ ] **Loki** — log aggregation
- [ ] **Monitoring coverage** — dashboards/alerts for every service
- [ ] **PV folder bootstrapping** — auto-create missing host paths
- [x] **Gateway API** — replaced Traefik `IngressRoute` with Gateway API (Envoy Gateway + Gateway API HTTPRoute/TCPRoute/UDPRoute) ✓
- [ ] **AWS Lambda** — provision via Crossplane for specific tasks
- [ ] **Smart home** — make the smarthome stack Kubernetes-API-native
- [x] **Fix namespace issue of Cert-Manager application apps/cert-manager/templates/cert-manager-application.yaml:8** — every Application object (parent + cert-manager-controller + envoy-gateway-controller) now derives `metadata.namespace` from `global.argocd.namespace`, eliminating the `$.Release.Namespace` footgun ✓
- [ ] **radicale only /api without oauth2** — `apps/radicale/values.yaml` has `oauth: false` on its HTTPS route (CalDAV clients use radicale's own auth)
- [ ] **hess.pm cloudlfare WAF, only german IPs** 

### Known quirks

- If the `envoy-gateway-controller` ArgoCD Application is freshly created or relocated to a new namespace, ArgoCD's automated sync may not run the upstream chart's `envoy-gateway-certgen` Helm hook. The controller pod then mounts a non-existent `envoy-gateway` Secret and gets stuck `ContainerCreating`. Recovery is one explicit sync:
  ```
  kubectl -n argocd patch application envoy-gateway-controller --type merge \
    -p '{"operation":{"sync":{}}}'
  ```
  Within ~30 s the certgen Job fires, the secret appears, the controller starts.

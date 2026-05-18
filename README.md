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
  kind-setup/            # Local Kind cluster for testing
  microk8s-setup/        # MicroK8s setup helpers
  zfs-exporter/          # Prometheus ZFS exporter installer
doc/                     # Operational notes (Cognito, Homematic, ZFS, Paperless, ...)
```

## Core Technologies

- **Kubernetes** — container orchestration runtime
- **Helm** — packaging and templating for all applications
- **ArgoCD** — declarative GitOps continuous delivery
- **Akeyless** + **External Secrets Operator** — secrets management
- **Traefik** — ingress controller (HTTPS via `IngressRoute`, TCP/UDP supported)
- **Crossplane** + **AWS controllers** — Kubernetes-managed cloud resources
- **Prometheus** / **Grafana** / **Alertmanager** — monitoring stack
- **Restic** + **Backrest** — backup orchestration
- **Renovate** — automated dependency updates
- **Cloudflare** — DNS and DDNS
- **ZFS** — underlying storage with compression and snapshots

## Getting Started

### Bootstrap a cluster
```bash
./scripts/setup.sh        # Installs ArgoCD and registers the base-chart
```

### Local testing with Kind
```bash
./scripts/kind-setup/deploy_kind_cluster.sh
./scripts/kind-setup/install_cloud-provider-kind.sh
```

### Add a new application
```bash
python scripts/helm-tools/create_app.py   # Interactive scaffolder
```
Then enable the app in `base-chart/values.yaml` under `apps.<name>` with its `homer`, `argocd`, and `syncWave` settings. See `CLAUDE.md` for the full template.

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
- **portal-document-downloader** / **pytr** — automated document fetchers

### AI & ML
- **Ollama** — local LLM runtime
- **OpenWebUI** — chat UI for Ollama
- **prompt-util** — prompt utility service

### Infrastructure
- **ArgoCD** — GitOps controller
- **Traefik** — ingress / reverse proxy
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
- **Grafana** — dashboards
- **Alertmanager** — alert routing
- **Duplicati Prometheus Exporter** — backup metrics
- **TGTG** — TooGoodToGo notifications

## Dependency Management

Renovate keeps Helm chart versions, Docker images, and language packages up to date. Highlights:

- Patch and minor updates auto-merge; major updates require manual approval.
- Custom regex managers detect ArgoCD `targetRevision` and inline image references.
- Configuration: `.renovaterc.json`. See [`RENOVATE.md`](RENOVATE.md) for usage and how to exclude charts.

## TODOs & Future Improvements

- [ ] **Restart Pod argocd-server** — automate restart to handle Akeyless secret race conditions
- [ ] **Per-app namespaces** — migrate apps off the shared `services` namespace
- [ ] **Loki** — log aggregation
- [ ] **Monitoring coverage** — dashboards/alerts for every service
- [ ] **PV folder bootstrapping** — auto-create missing host paths
- [ ] **Gateway API** — replace Traefik `IngressRoute` with Gateway API
- [ ] **AWS Lambda** — provision via Crossplane for specific tasks
- [ ] **Smart home** — make the smarthome stack Kubernetes-API-native
- [ ] **Fix namespace issue of Cert-Manager application apps/cert-manager/templates/cert-manager-application.yaml:8**
- [ ] **radicale only api without oauth2**
- [ ] **hess.pm cloudlfare WAF, only german IPs** 

### Envoy migration — refactor follow-ups

Findings from reviewing the Traefik → Envoy Gateway migration. Roughly in priority order.

**Latent bugs (will bite the next environment migration):**

- [ ] **`base-chart/application.yaml` crashes the whole env on a missing `helm` key** — line 52 does `$appConfig.argocd.helm.releaseName` unguarded; one *enabled* app without an `argocd.helm` block fails the entire bootstrap `helm template`, so every app stops syncing. base-chart ships `cert-manager: enabled: true` with **no `helm:` key** (`base-chart/values.yaml:841`), so every new environment crashes on first sync until it redeclares cert-manager. Fix: `{{- $helm := $appConfig.argocd.helm | default dict }}` in `application.yaml`, and/or add `helm: {values: {}}` to the cert-manager default.
- [ ] **`envoy-gateway-application.yaml:5` child-app namespace** — the `envoy-gateway-controller` child Application uses `namespace: {{ $.Release.Namespace }}`; works only because the parent app runs in `argocd`. Same latent bug as the (now-fixed) cert-manager one. Hardcode `namespace: argocd` — child ArgoCD Applications must live where the application-controller watches.

**Security:**

- [ ] **Per-route OIDC is fail-open** — `apps/generic/templates/security-policy.yaml` drops OIDC whenever `oauth` is absent (`{{- if not $ingress.oauth }}`); forgetting `oauth: true` on a new app silently leaves its route unauthenticated. Make `oauth` a **required** field per `ingress.https[]` entry in `apps/generic/values.schema.json` so the choice is always explicit (flipping the runtime default outright is a breaking change — would re-OIDC HA's `/api` route).

**Cleanup / drift:**

- [ ] **Remove vestigial `global.security.lanCIDRs`** — unused since the listener-split refactor; still declared in `base-chart/values.yaml:49` and `apps/generic/values.schema.json:84`, and the schema description ("LAN traffic is gated by source IP") is now factually wrong. ⚠️ When doing this, **keep the `global.security` map itself** (e.g. `security: {cloudflareOriginCA: ~}`) — `envoy-mtls-client-traffic-policy.yaml` and `httproute.yaml` do unguarded `.Values.global.security.cloudflareOriginCA`, which nil-pointers if the whole `security` block is gone. LAN-only environments (no `cloudflareOriginCA`, e.g. `raspi.zimmermann.lat`) rely on base-chart providing that map. Safer: guard the templates with `(.Values.global.security | default dict)`.
- [ ] **Fix `docs/gateway-and-oidc.md` Step 6** — claims `:10443` is the per-pod port for the `:4443` listener; access logs show it's actually the `:443` LAN listener (privileged ports get a +10000 offset; `:4443` binds directly).
- [ ] **`dns` exposed-port** — the chart default adds a `dns` UDP listener; environments without AdGuard (e.g. zimmermann.lat) leave it with 0 routes. Confirm `additionalExposedPorts: {dns: null}` overrides actually reach the deployed values (currently rendering as `{}`).
- [ ] **Rename Akeyless path `/oidc/oauth2-proxy/client_secret`** — name references oauth2-proxy, which no longer exists; rename to `/oidc/client_secret`.
- [ ] **`whoami` app** — still labelled "Debug Traefik" with a Traefik logo post-migration.

**Architectural notes (not bugs, worth tracking):**

- [ ] **cert-manager parent app namespace** — deploys into the `cert-manager` namespace while every other app uses `argocd`; that asymmetry is what made the child-app namespace bug subtle. All its resources are cluster-scoped or explicitly namespaced, so aligning the parent to `argocd` would remove the `$.Release.Namespace` footgun entirely.
- [ ] **LAN listener depends on internal DNS** — the `:443` LAN listener only receives traffic if LAN clients resolve `*.<domain>` to the gateway LB IP (internal DNS / HA `internal_url`). If that resolution breaks, LAN traffic silently hairpins out through Cloudflare — slower, and LAN access dies during internet outages. Add monitoring.

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
- [ ] **Per-app namespaces** — migrate apps off the shared `argocd` namespace. *Plumbing complete (May 2026)*: every app can opt in via `apps.<app>.argocd.namespace`; the generic chart auto-emits cross-namespace `ReferenceGrant`s for HTTPRoutes (immich and envoy-gateway are the apps with their own namespaces today). Mass migration of the remaining ~25 apps still outstanding; data-layer apps (postgres/redis/mosquitto) need FQDN connection strings in consumers when moved.
- [ ] **Loki** — log aggregation
- [ ] **Monitoring coverage** — dashboards/alerts for every service
- [ ] **PV folder bootstrapping** — auto-create missing host paths
- [x] **Gateway API** — replaced Traefik `IngressRoute` with Gateway API (Envoy Gateway + Gateway API HTTPRoute/TCPRoute/UDPRoute) ✓
- [ ] **AWS Lambda** — provision via Crossplane for specific tasks
- [ ] **Smart home** — make the smarthome stack Kubernetes-API-native
- [x] **Fix namespace issue of Cert-Manager application apps/cert-manager/templates/cert-manager-application.yaml:8** — every Application object (parent + cert-manager-controller + envoy-gateway-controller) now derives `metadata.namespace` from `global.argocd.namespace`, eliminating the `$.Release.Namespace` footgun ✓
- [ ] **radicale only /api without oauth2** — `apps/radicale/values.yaml` has `oauth: false` on its HTTPS route (CalDAV clients use radicale's own auth)
- [ ] **hess.pm cloudlfare WAF, only german IPs** 

### Envoy migration — refactor follow-ups

Findings from reviewing the Traefik → Envoy Gateway migration. The full
list was worked through in May 2026 on branch `envoy-migration-followups`.

**Latent bugs (would have bitten the next environment migration):**

- [x] **`base-chart/application.yaml` crashes the whole env on a missing `helm` key** — line 52 did `$appConfig.argocd.helm.releaseName` unguarded; one *enabled* app without an `argocd.helm` block failed the entire bootstrap `helm template`, so every app stopped syncing. Fixed by routing all `argocd.helm` lookups through `{{- $helm := $appConfig.argocd.helm | default dict }}` in `application.yaml`.
- [x] **`envoy-gateway-application.yaml:5` child-app namespace** — the `envoy-gateway-controller` child Application used `namespace: {{ $.Release.Namespace }}`, which only worked by coincidence (parent app happened to deploy into `argocd`). Same class of bug as the cert-manager child app. Both child Applications now derive `metadata.namespace` from `global.argocd.namespace` (single source of truth).

**Security:**

- [x] **Per-route OIDC is fail-open** — `apps/generic/values.schema.json` now requires `oauth` on every `ingress.https[]` entry. Every existing app's routes were swept and made explicit (`oauth: true` or `oauth: false`); behaviour at runtime is unchanged but the implicit fail-open footgun is gone. Any future HTTPS route added without an explicit `oauth` field fails schema validation at render time.

**Cleanup / drift:**

- [x] **Remove vestigial `global.security.lanCIDRs`** — unreferenced since the listener-split refactor; removed from `base-chart/values.yaml` and `apps/generic/values.schema.json`. Templates that dereference `.Values.global.security.cloudflareOriginCA` were guarded with `(.Values.global.security | default dict)` so LAN-only envs without `cloudflareOriginCA` don't nil-pointer.
- [x] **Fix `docs/gateway-and-oidc.md` Step 6** — corrected: the `:4443` Cloudflare listener binds directly; `:10443` is the per-pod port for the privileged `:443` LAN listener (privileged ports get a +10000 offset; non-privileged ones bind as-is).
- [x] **`dns` exposed-port** — verified: `additionalExposedPorts: {dns: null}` propagates correctly. base-chart's `application.yaml` serializes it into the child Application's `valuesObject` as `dns: null`, and Helm's coalesce drops the `dns` listener from the rendered Gateway. No code change needed.
- [x] **Rename Akeyless path `/oidc/oauth2-proxy/client_secret`** — done, plus consolidated. The path is now `/oidc/client_secret`, driven by `global.oidc.clientSecretAkeylessPath` in base-chart. Three former hardcodes (envoy-gateway, argocd-external-secret, sftpgo) all read the global. The generic chart's `external-secrets.yaml` now `tpl`-renders remote paths so values.yaml entries can reference globals via Helm syntax. Future Akeyless renames are a one-line change.
- [x] **`whoami` app** — relabelled "Debug Envoy Gateway"; Traefik logo swapped for a neutral icon (dashboard-icons has no Envoy icon).

**Architectural notes (originally tracked):**

- [ ] ~~**cert-manager parent app namespace**~~ — stale item. Originally flagged because cert-manager's parent app destination was `cert-manager` while every other parent pointed at `argocd`. With the per-app-namespace strategy (immich → `app-immich`, envoy-gateway → `envoy-gateway`, etc.), cert-manager being in its own namespace is now the *consistent* choice rather than the odd one out. No action.
- [x] **LAN listener depends on internal DNS** — addressed. New optional global `global.lanDnsCheck: {dnsServer, queryName}` activates: a `dns_lan_gateway` module in `prometheus-blackbox-exporter`, a `Probe` against the configured DNS server, and a `LanInternalDnsBroken` PrometheusRule that fires if the answer is not `global.gateway.loadBalancerIP` for 5+ minutes. Catches silent LAN→Cloudflare hairpinning. zimmermann.lat opts in; raspi (no setting) renders nothing.

**Additional refactors done in this pass (beyond the original list):**

- **Single sources of truth promoted to `global`:**
  - `global.argocd.namespace` — where ArgoCD Application objects live (was hardcoded in two places).
  - `global.gateway.namespace` — derived in base-chart from `apps.envoy-gateway.argocd.namespace` (was a duplicate hand-set value).
  - `global.gateway.loadBalancerIP` — the Gateway's LB IP (was nested in envoy-gateway's helm values and duplicated in samba's avahi config).
  - `global.oidc.clientSecretAkeylessPath` — Akeyless path for the OIDC client secret (was hardcoded in three places).
- **ReferenceGrant naming bug** — `apps/generic/templates/reference-grant.yaml` hardcoded the name `route-to-services`. Latent while the gateway shared a namespace with most apps (the guard kept the grant from rendering). The moment the gateway moved out of `argocd`, every app sharing `argocd` rendered an identically-named grant → ArgoCD shared-resource collision. Fixed: name is now `{{ $appName }}-route-to-services`.
- **envoy-gateway relocated to its own namespace** — Gateway CR, all envoy-gateway resources, the controller, the certgen Job, the control-plane TLS secrets, and the proxy data plane all now live in `envoy-gateway` (formerly split between `argocd` and `envoy-gateway-system`). The `envoy-gateway-system` namespace has been cleaned up and removed.
- **samba avahi `publishIP`** — now defaults to `global.gateway.loadBalancerIP` (no env override needed).
- **`docs/gateway-and-oidc.md`** — full pass to update every namespace reference and `kubectl` example to reflect the post-relocation reality.
- **Stale `traefik-forward-auth` references** — purged from `doc/Cognito/AWS_Cognito.md`, `doc/Cognito/sftpgo_oidc.md`, and `scripts/helm-tools/create_app.py` (the create_app interactive tool now prompts for `oauth: true|false` per the new schema rather than auto-injecting a Traefik middleware).
- **Generic chart bumped four times** during the pass (`0.1.51 → 0.1.55`) to propagate schema additions to every dependent app chart.

**Known quirk surfaced (not fixed in iac, recovery known):**

- If the `envoy-gateway-controller` ArgoCD Application is freshly created or relocated to a new namespace, ArgoCD's automated sync may not run the upstream chart's `envoy-gateway-certgen` Helm hook. The controller pod then mounts a non-existent `envoy-gateway` Secret and gets stuck `ContainerCreating`. Recovery is one explicit sync:
  ```
  kubectl -n argocd patch application envoy-gateway-controller --type merge \
    -p '{"operation":{"sync":{}}}'
  ```
  Within ~30 s the certgen Job fires, the secret appears, the controller starts.

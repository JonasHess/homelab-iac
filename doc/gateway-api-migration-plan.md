# Migration Plan: Traefik IngressRoute → Gateway API + cert-manager

## Context

The homelab currently uses Traefik-specific CRDs (`IngressRoute`, `IngressRouteTCP`, `IngressRouteUDP`) for all routing and Traefik's built-in ACME client for TLS certificates. This migration replaces those with standard Kubernetes Gateway API resources (`HTTPRoute`, `TCPRoute`, `UDPRoute`) and cert-manager for TLS, making the ingress layer portable and standards-based while keeping Traefik as the Gateway controller.

Breaking changes are accepted in favor of clean architecture.

---

## Key Architecture Decisions

1. **Keep Traefik as Gateway controller** — it supports Gateway API natively, and Traefik `Middleware` CRDs work with HTTPRoute via `ExtensionRef` filters
2. **All apps deploy to `argocd` namespace** (except immich → `app-immich`) — this means middleware CRDs (already in `argocd`) are automatically in the same namespace as HTTPRoutes, no cross-namespace issues
3. **Keep Cloudflare middleware, apply per-route** — the `httproute.yaml` template automatically injects the `cloudflare` ExtensionRef filter on every HTTPRoute (no per-app config needed)
4. **Single wildcard Certificate** managed by cert-manager replaces Traefik's ACME client
5. **Explicit `pathPrefixes` field** replaces Traefik-syntax `matchSuffix`; `priority` removed (Gateway API uses match specificity)
6. **`traefikEntryPoint` renamed to `listener`** (Gateway API terminology)
7. **Keep `kubernetesCRD` provider enabled** alongside `kubernetesGateway` — needed for Middleware CRD resolution via ExtensionRef

---

## Phase 1: Add cert-manager app

Independent from everything else — can be deployed first as a separate PR.

### New files

**`apps/cert-manager/Chart.yaml`**
- Standard chart depending on `generic` (current version 0.1.31)

**`apps/cert-manager/values.yaml`**
- ExternalSecret for Cloudflare API token: `cloudflare-api-credentials` with key `api-token` from `/acme/cloudflare-api-credentials_apiKey`
- `certManager.version: "v1.17.2"`

**`apps/cert-manager/templates/cert-manager-application.yaml`**
- ArgoCD Application deploying `cert-manager` Helm chart from `https://charts.jetstack.io`
- Target namespace: `cert-manager` (with `CreateNamespace=true`)
- Enable CRDs: `crds.enabled: true`
- Enable Gateway API support: `featureGates: "ExperimentalGatewayAPISupport=true"`
- sync-wave: `"0"` (within the cert-manager app)

**`apps/cert-manager/templates/cluster-issuer.yaml`**
- `ClusterIssuer` named `letsencrypt-production`
- ACME server: `https://acme-v02.api.letsencrypt.org/directory`
- Email from `global.letsencrypt.email`
- Cloudflare DNS-01 solver referencing `cloudflare-api-credentials` secret
- sync-wave: `"5"` (after cert-manager controller is ready)

### Modified files

**`base-chart/values.yaml`** — add cert-manager app entry:
```yaml
cert-manager:
  enabled: true
  argocd:
    targetRevision: ~
    namespace: "cert-manager"
    project: "default"
    syncWave: "1"
```

Note: The cert-manager app deploys to `cert-manager` namespace so the ExternalSecret for Cloudflare credentials lands there (where the ClusterIssuer expects it).

---

## Phase 2: Configure Traefik for Gateway API + create Gateway resource

### New files

**`apps/traefik/templates/gateway-api-crds.yaml`**
- ArgoCD Application that installs Gateway API CRDs (experimental channel) from `github.com/kubernetes-sigs/gateway-api` (pin to `v1.2.1`)
- Path: `config/crd/experimental` (includes TCPRoute, UDPRoute)
- sync-wave: `"-1"` (before everything else)

**`apps/traefik/templates/gateway.yaml`**
- `Gateway` resource named `traefik-gateway` in `argocd` namespace
- `gatewayClassName: traefik`
- Listeners:
  - `web` — port 80, HTTP, `allowedRoutes.namespaces.from: All`
  - `websecure` — port 443, HTTPS, hostname `*.{domain}`, TLS terminate, `certificateRefs: [{name: wildcard-tls}]`
  - `websecure-apex` — port 443, HTTPS, hostname `{domain}` (bare domain), same TLS config
  - Dynamic TCP/UDP listeners from `additionalExposedPorts` map (sftpgo/2222, ftp/2121, dns/53, plex/32400)
- sync-wave: `"5"`

**`apps/traefik/templates/wildcard-certificate.yaml`**
- cert-manager `Certificate` resource in `argocd` namespace
- `secretName: wildcard-tls`
- `issuerRef: {name: letsencrypt-production, kind: ClusterIssuer}`
- `dnsNames: ["{domain}", "*.{domain}"]`
- sync-wave: `"3"`

### Modified files

**`apps/traefik/templates/traefik-application.yaml`** — changes to Helm `valuesObject`:

Add:
```yaml
providers:
  kubernetesGateway:
    enabled: true
    experimentalChannel: true    # for TCPRoute/UDPRoute
gatewayClass:
  enabled: true
  name: traefik
gateway:
  enabled: false                 # we manage our own Gateway resource
api:
  insecure: true
  dashboard: true
ingressRoute:
  dashboard:
    enabled: false               # we create our own HTTPRoute for the dashboard
```

Remove:
- `websecure.http.middlewares` (global cloudflare middleware — now applied per-route via ExtensionRef)
- `websecure.http.tls.certResolver` and `websecure.http.tls.domains` (cert-manager handles TLS now)
- All 4 ACME `additionalArguments` (`--certificatesresolvers.cloudflare.*`)
- `CF_API_EMAIL` and `CF_DNS_API_TOKEN` env vars
- `persistence` block (no more ACME json storage needed)

Keep:
- HTTP→HTTPS redirect args (`--entrypoints.web.http.redirections.*`)
- `--serversTransport.insecureSkipVerify=true`
- `--entryPoints.web.proxyProtocol.insecure` and `forwardedHeaders.insecure`
- `kubernetesCRD` provider with `allowCrossNamespace: true`
- All port definitions (`web`, `websecure`, `additionalExposedPorts`)
- `experimental.plugins` block (cloudflare plugin — still needed for per-route middleware)
- LoadBalancer service config
- Security context, health probes

**`apps/traefik/values.yaml`** — remove:
- `generic.externalSecrets.cloudflare-api-credentials` (moved to cert-manager)
- `generic.persistentVolumeClaims.data` (no more ACME storage)

Keep:
- `middlewares.cloudflare` section (still used, now applied per-route via ExtensionRef)

**`apps/traefik/templates/traefik-traefik-dashboard.yaml`** — rewrite `IngressRoute` → `HTTPRoute`:
- `parentRefs: [{name: traefik-gateway, sectionName: websecure}]`
- `hostnames: [traefik.{domain}]`
- ExtensionRef filters for `cloudflare` + `oauth2-proxy` middlewares
- `backendRefs: [{name: traefik-ingress-controller, port: 9000}]` (Traefik API port)

**`apps/traefik/templates/oauth2-proxy/ingressroute.yaml`** — rewrite `IngressRoute` → `HTTPRoute`:
- `parentRefs: [{name: traefik-gateway, sectionName: websecure}]`
- `hostnames: [auth.{domain}]`
- ExtensionRef filter for `cloudflare` only (no oauth2-proxy — auth endpoint must be accessible without login)
- `backendRefs: [{name: {releaseName}-oauth2-proxy, port: 80}]`

### Unchanged files

- `apps/traefik/templates/cloudflare/traefik-plugin-cloudflare.yaml` — kept as-is, now referenced per-route via ExtensionRef
- `apps/traefik/templates/oauth2-proxy/middlewares.yaml` — Middleware CRDs stay exactly as-is (they work with Gateway API via ExtensionRef)
- `apps/traefik/templates/oauth2-proxy/templates-configmap.yaml` — unchanged

---

## Phase 3: Rewrite generic chart ingress templates

### New ingress values schema

```yaml
ingress:
  https:
    - subdomain: string          # optional (omit for bare domain)
      port: integer              # REQUIRED
      service: string            # optional (default: {appName}-service)
      middlewares: [string]      # optional (Traefik Middleware names)
      pathPrefixes: [string]     # optional (replaces matchSuffix)
      rawMatch: [object]         # optional (raw Gateway API HTTPRouteMatch for complex cases)
  tcp:
    - port: integer              # REQUIRED
      listener: string           # REQUIRED (Gateway listener name, was traefikEntryPoint)
  udp:
    - port: integer              # REQUIRED
      listener: string           # REQUIRED (Gateway listener name, was traefikEntryPoint)
```

Changes from current schema:
| Old field | New field | Notes |
|---|---|---|
| `matchSuffix` | `pathPrefixes` | List of path prefixes (OR'd together) |
| `matchSuffix` (complex) | `rawMatch` | Escape hatch for headers/advanced matching |
| `priority` | *(removed)* | Gateway API uses match specificity |
| `traefikEntryPoint` | `listener` | Gateway API terminology |

### New template files

**`apps/generic/templates/httproute.yaml`** (replaces `ingress-https.yaml`):
- Generates `gateway.networking.k8s.io/v1 HTTPRoute`
- `parentRefs` → `traefik-gateway` in `argocd`, sectionName `websecure` (or `websecure-apex` for bare domain)
- `hostnames` from subdomain + global.domain
- If `pathPrefixes` set → generates `matches[].path.type: PathPrefix` entries (OR'd)
- If `rawMatch` set → passes through as raw match objects
- If neither → no explicit match (catch-all)
- **Always injects `cloudflare` ExtensionRef filter first** (replaces the old global entry-point middleware)
- Additional `middlewares` → `filters[].type: ExtensionRef` with `group: traefik.io, kind: Middleware`
- `backendRefs` → service name + port

**`apps/generic/templates/tcproute.yaml`** (replaces `ingress-tcp.yaml`):
- Generates `gateway.networking.k8s.io/v1alpha2 TCPRoute`
- `parentRefs.sectionName` from `listener` field
- `backendRefs` → service name + port

**`apps/generic/templates/udproute.yaml`** (replaces `ingress-udp.yaml`):
- Generates `gateway.networking.k8s.io/v1alpha2 UDPRoute`
- Same structure as TCPRoute

### Deleted template files

- `apps/generic/templates/ingress-https.yaml`
- `apps/generic/templates/ingress-tcp.yaml`
- `apps/generic/templates/ingress-udp.yaml`

### Modified files

**`apps/generic/values.schema.json`** — update ingress section:
- Remove `matchSuffix`, `priority`, `traefikEntryPoint` properties
- Add `pathPrefixes` (array of strings), `rawMatch` (array of objects), `listener` (string)

**`apps/generic/values.yaml`** — replace `global.traefik.middlewareNamespace` with:
```yaml
global:
  gateway:
    name: traefik-gateway
    namespace: argocd
```

**`base-chart/values.yaml`** — replace `global.traefik.middlewareNamespace: argocd` with:
```yaml
global:
  gateway:
    name: traefik-gateway
    namespace: argocd
```

### Bump generic chart version

Run `cd apps && ./bump_generic_chart.sh` after all template/schema changes.

---

## Phase 4: Update all app values.yaml files

### Apps needing NO ingress changes (~20 apps)

These only use `subdomain`, `port`, `service`, `middlewares` — all unchanged:
whoami, freshrss, homer, tautulli, radicale, prompt-util, smarthome4-ui, prometheus, stirlingpdf, openwebui, audiobookshelf, mealie, immich, jellyfin, zigbee2mqtt, homematic, plex (HTTPS portion), vaultwarden, etc.

### Apps with simple `matchSuffix` → `pathPrefixes` (9 apps, identical pattern)

Remove `priority`, replace `matchSuffix: '&& PathPrefix(\`/api\`)'` with `pathPrefixes: [/api]`:

| File | matchSuffix → pathPrefixes |
|---|---|
| `apps/sonarr/values.yaml` | `/api` |
| `apps/radarr/values.yaml` | `/api` |
| `apps/readarr/values.yaml` | `/api` |
| `apps/prowlarr/values.yaml` | `/api` |
| `apps/profilarr/values.yaml` | `/api` |
| `apps/paperlessngx/values.yaml` | `/api` |
| `apps/qbittorrent/values.yaml` | `/api` |
| `apps/overseerr/values.yaml` | `/api` |
| `apps/backrest/values.yaml` | `/api` |

### Apps with multi-path `matchSuffix` → `pathPrefixes` (4 apps)

| File | pathPrefixes |
|---|---|
| `apps/nzbget/values.yaml` | `[/jsonrpc, /xmlrpc]` |
| `apps/sftpgo/values.yaml` | `[/web/client/pubshares/]` |
| `apps/nextcloud/values.yaml` (API) | `[/remote.php, /ocs, /status.php, /cron.php, /apps/richdocuments, /index.php/login, /login/v2]` |
| `apps/nextcloud/values.yaml` (Collabora) | `[/hosting/discovery, /hosting/capabilities, /cool, /browser, /lool, /loleaflet]` |

### Apps with complex matchSuffix → `pathPrefixes` + `rawMatch` (2 apps)

**`apps/homeassistant/values.yaml`:**
```yaml
    - subdomain: homeassistant
      port: 8123
      pathPrefixes:
        - /api
        - /auth
      rawMatch:
        - headers:
            - name: User-Agent
              type: RegularExpression
              value: ".*wv.*"
```

**`apps/sabnzbd/values.yaml`:** — drop `QueryRegexp` (not supported by Gateway API), keep only path match:
```yaml
    - subdomain: sabnzbd
      pathPrefixes:
        - /sabnzbd/api
      port: 8080
```

### TCP/UDP apps: rename `traefikEntryPoint` → `listener` (3 apps)

| File | Changes |
|---|---|
| `apps/plex/values.yaml` | `traefikEntryPoint: plex` → `listener: plex` |
| `apps/sftpgo/values.yaml` | `traefikEntryPoint: sftpgo` → `listener: sftpgo`, `traefikEntryPoint: ftp` → `listener: ftp` |
| `apps/adguard/values.yaml` | `traefikEntryPoint: dns` → `listener: dns` |

### Custom IngressRoute template (1 app)

**`apps/argocd/templates/argocd-ingress-route.yaml`** → rewrite as HTTPRoute:
- Rename file to `argocd-httproute.yaml`
- `parentRefs: [{name: global.gateway.name, namespace: global.gateway.namespace, sectionName: websecure}]`
- `hostnames: [argocd.{domain}]`
- Rule 1: `matches: [{path: {type: PathPrefix, value: /api/webhook}}]` + ExtensionRef `cloudflare` → backendRefs to `argocd-server:443` (no oauth)
- Rule 2: catch-all with ExtensionRef `cloudflare` + `oauth2-proxy` → backendRefs to `argocd-server:443`
- Note: `serversTransport` is no longer needed per-route since `--serversTransport.insecureSkipVerify=true` is global

---

## Phase 5: Cleanup

- Delete `apps/generic/templates/ingress-https.yaml`, `ingress-tcp.yaml`, `ingress-udp.yaml`
- Update `CLAUDE.md` documentation references (replace IngressRoute/certResolver/matchSuffix/traefikEntryPoint with Gateway API equivalents)
- Update `docs/traefik-ingress-setup.md` or mark it as superseded

---

## Deployment Strategy

**PR 1 (independent):** Phase 1 only — deploy cert-manager + ClusterIssuer. Can coexist with existing ACME setup.

**PR 2 (atomic):** Phases 2–5 together — all routing changes must happen in one commit since old templates are deleted and new ones replace them simultaneously. ArgoCD auto-syncs on push.

Expected brief downtime during PR 2 sync as old IngressRoute resources are replaced by HTTPRoute resources.

---

## Verification

1. **After PR 1:** Check cert-manager pods running in `cert-manager` namespace; verify ClusterIssuer is Ready
2. **After PR 2:**
   - `kubectl get gateway -n argocd` → traefik-gateway with programmed listeners
   - `kubectl get certificate -n argocd` → wildcard-tls Ready
   - `kubectl get httproute -n argocd` → all app routes listed
   - `kubectl get tcproute -n argocd` → plex, sftpgo routes
   - `kubectl get udproute -n argocd` → adguard DNS route
   - Test HTTPS access to any app subdomain (check cert is valid Let's Encrypt wildcard)
   - Test oauth2-proxy flow (visit protected app → redirect to Cognito → redirect back)
   - Test TCP: SFTP to port 2222, FTP to port 2121
   - Test UDP: DNS query to port 53
   - Test API bypass routes (e.g., `curl https://sonarr.{domain}/api/...` without auth)

---

## File Inventory Summary

| Action | Count | Files |
|---|---|---|
| New | 10 | cert-manager (4), traefik (3), generic templates (3) |
| Delete | 3 | generic old templates (3) |
| Modify | ~25 | traefik app/values/templates (5), generic schema/values (2), base-chart (1), argocd template (1), app values (~16) |

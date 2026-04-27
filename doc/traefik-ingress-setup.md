# Traefik Ingress Setup Documentation

> Reference documentation for the current Traefik-based ingress architecture.
> Intended to serve as a migration guide when moving to Gateway API.

---

## Table of Contents

1. [Overview](#overview)
2. [Traefik Deployment](#traefik-deployment)
3. [Entry Points](#entry-points)
4. [HTTPS Ingress (IngressRoute)](#https-ingress-ingressroute)
5. [TCP Ingress (IngressRouteTCP)](#tcp-ingress-ingressroutetcp)
6. [UDP Ingress (IngressRouteUDP)](#udp-ingress-ingressrouteudp)
7. [TLS / SSL Certificates](#tls--ssl-certificates)
8. [Middleware](#middleware)
9. [Authentication (OAuth2-Proxy)](#authentication-oauth2-proxy)
10. [Per-App Ingress Inventory](#per-app-ingress-inventory)
11. [Generic Chart Ingress Schema](#generic-chart-ingress-schema)
12. [Key Files Reference](#key-files-reference)
13. [Gateway API Migration Notes](#gateway-api-migration-notes)

---

## Overview

All external traffic enters the cluster through a single **Traefik** instance deployed as a `LoadBalancer` Service in the `argocd` namespace. Traefik is configured entirely through its Helm chart (upstream `traefik/traefik` v39.0.2) and Traefik-specific CRDs (`IngressRoute`, `IngressRouteTCP`, `IngressRouteUDP`, `Middleware`).

There are **no standard Kubernetes `Ingress` resources** in use. Everything uses Traefik's native CRD API (`traefik.io/v1alpha1`).

### Architecture Diagram

```
Internet
   │
   ▼
Cloudflare DNS (*.domain → cluster IP)
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Traefik LoadBalancer Service                           │
│                                                         │
│  Port 80 (web) ──────► HTTP→HTTPS redirect              │
│  Port 443 (websecure) ──► HTTPS routes (IngressRoute)   │
│  Port 2222 (sftpgo) ─────► TCP route (IngressRouteTCP)  │
│  Port 2121 (ftp) ─────────► TCP route (IngressRouteTCP) │
│  Port 53 (dns) ───────────► UDP route (IngressRouteUDP) │
│  Port 32400 (plex) ──────► TCP route (IngressRouteTCP)  │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Middleware Chain (per-route, optional)                  │
│                                                         │
│  1. cloudflare plugin  ─ Cloudflare IP validation       │
│  2. oauth2-proxy chain ─ OIDC authentication            │
└─────────────────────────────────────────────────────────┘
   │
   ▼
  Kubernetes Services → Pods
```

---

## Traefik Deployment

Traefik is deployed as a **child ArgoCD Application** from within the `apps/traefik/` chart.

| Property | Value |
|---|---|
| Helm chart | `traefik/traefik` from `https://helm.traefik.io/traefik` |
| Chart version | `39.0.2` |
| Namespace | `argocd` |
| Service type | `LoadBalancer` |
| External traffic policy | `Local` |
| Sync wave | `1` (deployed early, after ArgoCD itself) |
| Persistence | PVC `traefik-data-pvc` mounted at `/data` (stores ACME cert data) |

**Source file:** `apps/traefik/templates/traefik-application.yaml`

### CRD Provider Settings

```yaml
providers:
  kubernetesCRD:
    enabled: true
    allowCrossNamespace: true   # IngressRoutes can reference services/middlewares in other namespaces
```

### Security Context

Traefik runs as root (`runAsUser: 0`) with `NET_BIND_SERVICE` capability to bind to privileged ports. The filesystem is read-only.

---

## Entry Points

Entry points are the network listeners that Traefik exposes. They are configured in the Traefik Helm values inside `traefik-application.yaml`.

### Standard Entry Points

| Name | Port | Protocol | Purpose | Notes |
|---|---|---|---|---|
| `web` | 80 | HTTP | HTTP listener | Permanently redirects all traffic to `websecure` (HTTPS) |
| `websecure` | 443 | HTTPS | Main HTTPS listener | TLS termination via ACME/Cloudflare, default middleware: `cloudflare` plugin |

Both `web` and `websecure` have generous timeouts configured:
```yaml
transport:
  respondingTimeouts:
    readTimeout: 30m
    writeTimeout: 30m
    idleTimeout: 30m
```

### Custom Entry Points (TCP/UDP)

Defined in `apps/traefik/values.yaml` under `additionalExposedPorts` and templated into the Traefik Helm values:

| Name | Port | Protocol | Used By |
|---|---|---|---|
| `sftpgo` | 2222 | TCP | SFTPGo (SFTP) |
| `ftp` | 2121 | TCP | SFTPGo (FTP) |
| `dns` | 53 | UDP | AdGuard Home (DNS) |

Additionally, the **plex** entry point (port 32400, TCP) is defined in environment-specific values (not in the default `values.yaml`), used by the Plex app.

### How Custom Entry Points Are Templated

```yaml
# In traefik-application.yaml:
ports:
  {{- range $key, $value := .Values.additionalExposedPorts }}
  {{ $key }}:
    port: {{ $value.port }}
    expose:
      default: true
    exposedPort: {{ $value.port }}
    protocol: {{ $value.protocol }}
  {{- end }}
```

Each entry point becomes a port on the Traefik LoadBalancer Service.

---

## HTTPS Ingress (IngressRoute)

**CRD:** `traefik.io/v1alpha1 / IngressRoute`
**Template:** `apps/generic/templates/ingress-https.yaml`

### How It Works

Every app that needs HTTPS exposure defines entries under `ingress.https[]` in its `values.yaml`. The generic chart template renders one `IngressRoute` resource per entry.

### Template Logic

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: {{ appName }}-https-{{ port }}-{{ index }}
  namespace: {{ .Release.Namespace }}
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`{{ subdomain }}.{{ domain }}`) {{ matchSuffix }}
      kind: Rule
      priority: {{ default 10 priority }}
      services:
        - name: {{ appName }}-service    # or custom service name
          port: {{ port }}
      middlewares:                        # optional
        - name: {{ middleware }}
          namespace: {{ middlewareNamespace }}
  tls:
    certResolver: cloudflare             # always uses ACME DNS challenge
```

### Values Schema for HTTPS Ingress

```yaml
ingress:
  https:
    - subdomain: myapp          # → myapp.example.com (optional; omit for bare domain)
      port: 8080                # required: service port to route to
      middlewares:              # optional: list of middleware names
        - oauth2-proxy
      matchSuffix: '&& PathPrefix(`/api`)'   # optional: additional Traefik rule matchers
      priority: 20              # optional: route priority (default 10, higher = matched first)
      service: custom-svc       # optional: override service name (default: {appName}-service)
```

### Route Matching

- **Host-based:** `Host(\`subdomain.domain\`)`
- **Path-based (via matchSuffix):** appended to host match, e.g. `&& PathPrefix(\`/api\`)`
- **Header-based (via matchSuffix):** e.g. `&& HeaderRegexp(\`User-Agent\`, \`wv\`)`
- **Query-based (via matchSuffix):** e.g. `&& QueryRegexp(\`apikey\`, \`^[a-z0-9]{32}$\`)`

### Common Pattern: Dual Routes (Auth + Bypass)

Many apps define two routes for the same subdomain:
1. **Low priority (10)**: Main UI with `oauth2-proxy` middleware
2. **High priority (20)**: API/specific paths without auth (for programmatic access)

Example from Home Assistant:
```yaml
ingress:
  https:
    # Main UI - protected by OAuth2
    - subdomain: homeassistant
      port: 8123
      middlewares:
        - oauth2-proxy
    # API + mobile app - no auth middleware (app handles its own auth)
    - subdomain: homeassistant
      priority: 20
      matchSuffix: '&& (PathPrefix(`/api`) || PathPrefix(`/auth`) || HeaderRegexp(`User-Agent`, `wv`))'
      port: 8123
```

### Global Middleware on websecure

The `websecure` entry point has a **default middleware** applied to ALL requests before route-specific middlewares:

```yaml
websecure:
  http:
    middlewares:
      - "argocd-cloudflare@kubernetescrd"
```

This applies the Cloudflare plugin middleware globally (validates Cloudflare headers, overwrites `X-Forwarded-For` with the real client IP).

---

## TCP Ingress (IngressRouteTCP)

**CRD:** `traefik.io/v1alpha1 / IngressRouteTCP`
**Template:** `apps/generic/templates/ingress-tcp.yaml`

### How It Works

TCP ingress provides **Layer 4 passthrough** — Traefik does not inspect or modify the traffic. Each TCP route requires a dedicated entry point (port).

### Template Logic

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRouteTCP
metadata:
  name: {{ appName }}-tcp-{{ port }}
spec:
  entryPoints:
    - {{ traefikEntryPoint }}     # must match an entry point defined in Traefik config
  routes:
    - match: HostSNI(`*`)         # matches everything (no SNI filtering)
      services:
        - name: {{ appName }}-service
          port: {{ port }}
```

### Values Schema for TCP Ingress

```yaml
ingress:
  tcp:
    - port: 2222                    # required: service port
      traefikEntryPoint: sftpgo     # required: name of the Traefik entry point
```

### Key Characteristics

- **No TLS termination** — traffic is passed through as-is
- **`HostSNI(\`*\`)`** — matches all SNI values (since there's one service per entry point, no need to filter)
- **No middleware support** — TCP routes don't support Traefik middlewares
- **1:1 mapping** — each TCP service needs its own dedicated port/entry point on the LoadBalancer

### Current TCP Routes

| App | Service Port | Entry Point | External Port |
|---|---|---|---|
| SFTPGo (SFTP) | 2222 | `sftpgo` | 2222 |
| SFTPGo (FTP) | 2121 | `ftp` | 2121 |
| Plex | 32400 | `plex` | 32400 |

---

## UDP Ingress (IngressRouteUDP)

**CRD:** `traefik.io/v1alpha1 / IngressRouteUDP`
**Template:** `apps/generic/templates/ingress-udp.yaml`

### Template Logic

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRouteUDP
metadata:
  name: {{ appName }}-udp-{{ port }}
spec:
  entryPoints:
    - {{ traefikEntryPoint }}
  routes:
    - services:
        - name: {{ appName }}-service
          port: {{ port }}
```

### Values Schema for UDP Ingress

```yaml
ingress:
  udp:
    - port: 53                   # required: service port
      traefikEntryPoint: dns     # required: name of the Traefik entry point
```

### Key Characteristics

- **No match rules** — UDP routes have no matching logic (stateless protocol)
- **No TLS, no middleware**
- **1:1 mapping** — each UDP service needs its own dedicated port/entry point

### Current UDP Routes

| App | Service Port | Entry Point | External Port |
|---|---|---|---|
| AdGuard Home (DNS) | 53 | `dns` | 53 |

---

## TLS / SSL Certificates

### Certificate Strategy: Wildcard via ACME DNS Challenge

All HTTPS traffic uses a **single wildcard certificate** obtained from Let's Encrypt via Cloudflare DNS-01 challenge.

| Property | Value |
|---|---|
| Certificate type | Wildcard (`*.domain` + bare `domain`) |
| ACME provider | Let's Encrypt |
| Challenge type | DNS-01 (via Cloudflare API) |
| DNS provider | Cloudflare |
| Cert resolver name | `cloudflare` |
| Storage location | `/data/dns_acme.json` (inside PVC `traefik-data-pvc`) |
| Renewal | Automatic (handled by Traefik's built-in ACME client) |
| Notification email | Configured via `global.letsencrypt.email` |

### How Certificates Are Configured

**1. Cert resolver definition** (via Traefik CLI arguments in `traefik-application.yaml`):

```yaml
additionalArguments:
  - --certificatesresolvers.cloudflare.acme.dnschallenge.provider=cloudflare
  - --certificatesresolvers.cloudflare.acme.email={{ .Values.global.letsencrypt.email }}
  - --certificatesresolvers.cloudflare.acme.dnschallenge.resolvers=1.1.1.1
  - --certificatesresolvers.cloudflare.acme.storage=/data/dns_acme.json
```

**2. Wildcard domain on the websecure entry point**:

```yaml
ports:
  websecure:
    http:
      tls:
        enabled: true
        certResolver: "cloudflare"
        domains:
          - main: {{ .Values.global.domain }}
            sans:
              - "*.{{ .Values.global.domain }}"
```

**3. Every IngressRoute references the cert resolver**:

```yaml
spec:
  tls:
    certResolver: cloudflare
```

### Cloudflare API Credentials

Stored in Akeyless, fetched via External Secrets Operator into a Kubernetes Secret `cloudflare-api-credentials`:

```yaml
generic:
  externalSecrets:
    cloudflare-api-credentials:
      - email: "/acme/cloudflare-api-credentials_email"
      - apiKey: "/acme/cloudflare-api-credentials_apiKey"
```

Injected into the Traefik pod as environment variables:

```yaml
env:
  - name: CF_API_EMAIL
    valueFrom:
      secretKeyRef:
        key: email
        name: cloudflare-api-credentials
  - name: CF_DNS_API_TOKEN
    valueFrom:
      secretKeyRef:
        key: apiKey
        name: cloudflare-api-credentials
```

The Cloudflare API token needs permissions: `Zone.DNS:Edit`, `Zone.Zone:Read` for all zones in the account.

### Certificate Flow

```
1. IngressRoute created with tls.certResolver: cloudflare
2. Traefik checks /data/dns_acme.json for existing cert
3. If missing or expiring:
   a. Traefik contacts Let's Encrypt (ACME)
   b. Let's Encrypt issues DNS-01 challenge
   c. Traefik creates TXT record via Cloudflare API (using CF_DNS_API_TOKEN)
   d. Cloudflare DNS resolvers (1.1.1.1) verify the challenge
   e. Let's Encrypt issues the certificate
   f. Traefik stores cert in /data/dns_acme.json
4. Traefik serves the certificate for all matching domains
```

### TCP/UDP Routes and TLS

TCP and UDP routes do **not** use TLS termination at the Traefik level. Traffic is passed through as raw TCP/UDP. If the upstream service needs TLS, it must handle it itself.

---

## Middleware

Middlewares are Traefik CRDs that modify requests before they reach the backend service. All middlewares are deployed in the `argocd` namespace.

### Middleware Namespace

Configured globally in `base-chart/values.yaml`:

```yaml
global:
  traefik:
    middlewareNamespace: argocd
```

All app IngressRoutes reference middlewares with this namespace.

### Available Middlewares

#### 1. Cloudflare Plugin (`cloudflare`)

**File:** `apps/traefik/templates/cloudflare/traefik-plugin-cloudflare.yaml`

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: cloudflare
  namespace: argocd
spec:
  plugin:
    traefik-plugin-cloudflare:
      allowedCIDRs: []              # Can restrict to Cloudflare IPs
      overwriteRequestHeader: true  # Trusts CF-Connecting-IP header
```

- **Applied globally** to the `websecure` entry point via `argocd-cloudflare@kubernetescrd`
- Uses the experimental Traefik plugin system (`github.com/agence-gaya/traefik-plugin-cloudflare` v1.2.0)
- Rewrites `X-Forwarded-For` with the real client IP from Cloudflare headers

#### 2. OAuth2-Proxy Chain (`oauth2-proxy`)

**File:** `apps/traefik/templates/oauth2-proxy/middlewares.yaml`

This is a **chain of two middlewares**:

**a. `oauth2-proxy-errors`** — Error page middleware:
```yaml
spec:
  errors:
    status: ["401"]
    query: /oauth2/sign_in?rd={url}
    service:
      name: traefik-oauth2-proxy
      port: 80
```
Catches 401 responses and redirects to the OAuth2 sign-in page with a return URL.

**b. `oauth2-proxy-auth`** — ForwardAuth middleware:
```yaml
spec:
  forwardAuth:
    address: http://traefik-oauth2-proxy.argocd.svc.cluster.local/oauth2/auth
    trustForwardHeader: true
    authResponseHeaders:
      - X-Auth-Request-User
      - X-Auth-Request-Email
```
Forwards each request to oauth2-proxy for authentication. Passes user identity headers to the backend.

**c. `oauth2-proxy`** — Chain combining both:
```yaml
spec:
  chain:
    middlewares:
      - name: oauth2-proxy-errors
      - name: oauth2-proxy-auth
```

**This is the middleware apps reference** in their `values.yaml`:
```yaml
middlewares:
  - oauth2-proxy
```

---

## Authentication (OAuth2-Proxy)

### Setup

OAuth2-proxy is deployed as a subchart of the Traefik chart (`oauth2-proxy/oauth2-proxy` v10.1.4).

| Property | Value |
|---|---|
| OIDC Provider | AWS Cognito |
| Issuer URL | `https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_t9DQfKlSX` |
| Auth endpoint | `auth.{domain}` (IngressRoute in argocd namespace) |
| Upstream | `static://202` (returns 202 on success, used with ForwardAuth) |
| Cookie | Secure, SameSite=none |

### Secrets

Fetched from Akeyless via External Secrets:

```yaml
generic:
  externalSecrets:
    oauth2-proxy:
      - client-id: "/oidc/oauth2-proxy/client_id"
      - client-secret: "/oidc/oauth2-proxy/client_secret"
      - cookie-secret: "/oidc/oauth2-proxy/cookie_secret"
```

### Custom Sign-In Template

A custom `sign_in.html` template auto-redirects to the OIDC provider (no manual "Sign in" button click required):

```html
<!-- apps/traefik/templates/oauth2-proxy/templates-configmap.yaml -->
<meta http-equiv="refresh" content="0;url=https://auth.{domain}/oauth2/start?rd={{.Redirect}}">
```

### Auth Flow

```
1. User visits https://app.domain/
2. Traefik applies cloudflare middleware (global)
3. Traefik applies oauth2-proxy middleware chain (per-route):
   a. ForwardAuth sends subrequest to oauth2-proxy /oauth2/auth
   b. If valid cookie → 202 → request passes through
   c. If no cookie → 401 → errors middleware catches it
   d. Redirect to /oauth2/sign_in?rd={original_url}
   e. Custom template auto-redirects to Cognito
   f. User authenticates with Cognito
   g. Cognito redirects back to oauth2-proxy callback
   h. oauth2-proxy sets session cookie
   i. User is redirected to original URL
4. Backend receives request with X-Auth-Request-User/Email headers
```

---

## Per-App Ingress Inventory

### Apps with HTTPS Ingress Only (with OAuth2-Proxy)

| App | Subdomain | Port | Has API Bypass |
|---|---|---|---|
| Homer | (root domain + subdomain) | 8080 | No |
| SFTPGo | `sftpgo` | 8080 | Yes (pubshares path) |
| Sonarr | `sonarr` | 8989 | Yes (`/api`) |
| Radarr | `radarr` | 7878 | Yes (`/api`) |
| Readarr | `readarr` | 8787 | Yes (`/api`) |
| Prowlarr | `prowlarr` | 9696 | Yes (`/api`) |
| Profilarr | `profilarr` | 6868 | Yes (`/api`) |
| Overseerr | `overseerr` | 5055 | Yes (`/api`) |
| qBittorrent | `qbittorrent` | 8080 | Yes (`/api`) |
| SABnzbd | `sabnzbd` | 8080 | Yes (`/sabnzbd/api` + apikey query) |
| NZBGet | `nzbget` | 6789 | Yes (`/jsonrpc`, `/xmlrpc`) |
| Paperless-ngx | `paperlessngx` | 8000 | Yes (`/api`) |
| Home Assistant | `homeassistant` | 8123 | Yes (`/api`, `/auth`, WebView UA) |
| Prometheus | `prometheus` | 9090 | No |
| Grafana | `grafana` | 80 | No |
| Alertmanager | `alertmanager` | 9093 | No |
| AdGuard | `adguard` + `adguard-admin` | 80, 3000 | No |
| Stirling PDF | `pdf` (+ 2nd subdomain) | 8080 | No |
| Zigbee2MQTT | varies | 8080 | No |
| Backrest | `backrest` | 9898 | Yes (`/api`) |
| Open WebUI | (2 subdomains) | 8080 | No |
| Nextcloud | `nextcloud` | 8080 | Yes (DAV, OCS, login, Collabora paths) |
| smarthome4-ui | `lights` | 8000 | No |
| FreshRSS | varies | 80 | No |
| Mealie | varies | 9000 | No |
| Radicale | varies | 5232 | No |
| Whoami | varies | 80 | No |
| Homematic | varies | 8181 | No |
| Tautulli | varies | 8181 | No |
| Prompt Util | varies | 8000 | No |

### Apps with HTTPS Without OAuth2-Proxy

| App | Subdomain | Port | Notes |
|---|---|---|---|
| Plex | `plex` | 32400 | Has its own auth |
| SFTPGo WebDAV | `webdav` | 8081 | WebDAV protocol doesn't support browser auth |
| Audiobookshelf | varies | 80 | OAuth commented out (has own auth) |
| Immich | varies | varies | Has own auth |
| Jellyfin | varies | varies | Has own auth |

### Apps with TCP Ingress

| App | Service Port | Entry Point Name | External Port |
|---|---|---|---|
| SFTPGo (SFTP) | 2222 | `sftpgo` | 2222 |
| SFTPGo (FTP) | 2121 | `ftp` | 2121 |
| Plex | 32400 | `plex` | 32400 |

### Apps with UDP Ingress

| App | Service Port | Entry Point Name | External Port |
|---|---|---|---|
| AdGuard Home (DNS) | 53 | `dns` | 53 |

---

## Generic Chart Ingress Schema

From `apps/generic/values.schema.json`, the ingress configuration accepts:

```json
{
  "ingress": {
    "type": "object",
    "properties": {
      "https": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["port"],
          "properties": {
            "port":        { "type": "integer" },
            "subdomain":   { "type": "string" },
            "matchSuffix": { "type": "string" },
            "priority":    { "type": "integer" },
            "service":     { "type": "string" },
            "middlewares":  { "type": "array", "items": { "type": "string" } }
          }
        }
      },
      "tcp": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["port", "traefikEntryPoint"],
          "properties": {
            "port":               { "type": "integer" },
            "traefikEntryPoint":  { "type": "string" }
          }
        }
      },
      "udp": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["port", "traefikEntryPoint"],
          "properties": {
            "port":               { "type": "integer" },
            "traefikEntryPoint":  { "type": "string" }
          }
        }
      }
    }
  }
}
```

---

## Key Files Reference

| File | Purpose |
|---|---|
| `apps/traefik/templates/traefik-application.yaml` | ArgoCD Application that deploys Traefik Helm chart with all configuration |
| `apps/traefik/values.yaml` | Traefik app values: extra ports, secrets, oauth2-proxy config |
| `apps/traefik/Chart.yaml` | Dependencies: generic chart + oauth2-proxy subchart |
| `apps/generic/templates/ingress-https.yaml` | Template: generates `IngressRoute` for HTTPS |
| `apps/generic/templates/ingress-tcp.yaml` | Template: generates `IngressRouteTCP` for TCP |
| `apps/generic/templates/ingress-udp.yaml` | Template: generates `IngressRouteUDP` for UDP |
| `apps/traefik/templates/oauth2-proxy/middlewares.yaml` | ForwardAuth + errors + chain middleware definitions |
| `apps/traefik/templates/oauth2-proxy/ingressroute.yaml` | IngressRoute for `auth.{domain}` |
| `apps/traefik/templates/oauth2-proxy/templates-configmap.yaml` | Custom auto-redirect sign-in template |
| `apps/traefik/templates/cloudflare/traefik-plugin-cloudflare.yaml` | Cloudflare plugin middleware |
| `apps/traefik/templates/traefik-traefik-dashboard.yaml` | IngressRoute for Traefik dashboard |
| `base-chart/values.yaml` | Global settings: domain, middleware namespace, OIDC config |

---

## Gateway API Migration Notes

This section captures Traefik-specific patterns that will need equivalents when migrating to Gateway API.

### CRD Mapping

| Traefik CRD | Gateway API Equivalent |
|---|---|
| `IngressRoute` | `HTTPRoute` |
| `IngressRouteTCP` | `TCPRoute` |
| `IngressRouteUDP` | `UDPRoute` |
| `Middleware` (ForwardAuth) | `Policy` or provider-specific extension |
| `Middleware` (Chain) | Multiple `backendRefs` filters or policy attachments |
| `Middleware` (Errors) | No direct equivalent — needs custom solution |
| `Middleware` (Plugin) | No direct equivalent — needs provider-specific extension |

### Key Differences to Address

1. **TLS Certificates**: Gateway API uses `Gateway` resource with `tls.certificateRefs` pointing to Kubernetes `Secret` resources. The ACME cert-manager pattern (cert-manager + `Certificate` CRD) replaces Traefik's built-in ACME client. You'll need:
   - cert-manager deployed in the cluster
   - A `ClusterIssuer` or `Issuer` for Let's Encrypt with Cloudflare DNS-01
   - `Certificate` resources requesting the wildcard cert
   - The `Gateway` resource references the resulting TLS Secret

2. **Entry Points → Gateway Listeners**: Each Traefik entry point maps to a `Gateway.spec.listeners[]` entry. Custom TCP/UDP ports become additional listeners.

3. **Middleware → Policies / Filters**:
   - OAuth2 ForwardAuth → Gateway API has no native ForwardAuth. Options:
     - Use an `ExtensionRef` filter if the Gateway controller supports it
     - Deploy an external auth service and use `HTTPRoute` filters
     - Use a service mesh sidecar pattern
   - Cloudflare plugin → Would need to be handled at the Gateway controller level or via a separate proxy

4. **matchSuffix Pattern**: The `matchSuffix` concatenation pattern (`Host(...) && PathPrefix(...)`) maps to Gateway API's structured matching:
   ```yaml
   # Gateway API equivalent
   matches:
     - path:
         type: PathPrefix
         value: /api
       headers:
         - name: Host
           value: app.domain
   ```

5. **Route Priority**: Gateway API uses match specificity for precedence (more specific matches win). The explicit `priority` field doesn't exist — route ordering is by specificity.

6. **Cross-Namespace References**: Gateway API uses `ReferenceGrant` resources to allow cross-namespace references, replacing Traefik's `allowCrossNamespace: true`.

7. **Additional Ports**: In Gateway API, each non-HTTP port would be a separate `Listener` on the `Gateway` resource, with `TCPRoute` or `UDPRoute` attached.

8. **Global Middleware**: The pattern of applying middleware globally to an entry point (cloudflare on websecure) would need to be handled via Gateway-level policy attachment or by adding filters to every HTTPRoute.

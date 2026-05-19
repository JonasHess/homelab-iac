# Gateway, OIDC, and auth

How HTTPS traffic is terminated, authenticated and authorised in this homelab.

## TL;DR

- **Envoy Gateway** (upstream `oci://docker.io/envoyproxy/gateway-helm` v1.7.2) is the Gateway-API controller. Installed by `apps/envoy-gateway/`.
- One `Gateway` resource, `envoy-gateway` in the `argocd` namespace, with **two HTTPS listener pairs**:
  - **`websecure` / `websecure-apex` (port 443, LAN-only).** MetalLB announces the LoadBalancer IP only on the LAN VLAN, and the router does **not** NAT `WAN:443` to it. Anything reaching this listener is on the LAN. No further IP filtering needed.
  - **`websecure-public` / `websecure-public-apex` (port 4443, Cloudflare-only).** Router NATs `WAN:443 → :4443`. The listener requires **mTLS**: every request must present a client certificate signed by `global.security.cloudflareOriginCA`. The matching leaf cert is uploaded to Cloudflare as the **Authenticated Origin Pulls (AOP) custom certificate**, so only requests proxied through *our* Cloudflare zone can complete the handshake. The TLS handshake itself is the gate — no shared headers, no IP allowlist.
- One **Gateway-level `SecurityPolicy`** (`gateway-oidc`) attaches **only** native Envoy OIDC against AWS Cognito. No `authorization` block — access control happens at the listener layer.
- **HTTPRoutes have dual `parentRefs`**: each route attaches to both the LAN listener and the Cloudflare listener (gated on `global.security.cloudflareOriginCA` being set), so the same hostname works from either path.
- Per-route `SecurityPolicy` resources are emitted **only to opt a route out of OIDC** (e.g. `/api` paths, webhooks). They contain `authorization: { defaultAction: Allow }` — the body is meaningless on its own, but Envoy Gateway's **full-replace** precedence means the route-level SP completely overrides the Gateway-level SP, dropping OIDC for that route.
- A Gateway-level **`EnvoyExtensionPolicy`** (`strip-oauth-cookies`) runs a Lua filter that strips Envoy's encrypted OIDC cookies from the request before it reaches the upstream pod, so Node.js (16 KB) and Tomcat (~8 KB) header limits never see them.
- Client IP detection uses `customHeader: CF-Connecting-IP, failClosed: false`. Cloudflare-proxied requests yield the real visitor IP from the header; LAN-direct requests fall back to the TCP source IP.
- One Cognito callback URL: `https://auth.<domain>/oauth2/callback`. Cookies are scoped to the parent domain (`cookieDomain: <domain>`), so one Cognito sign-in covers every subdomain.

## Components

### `apps/envoy-gateway/`

The chart that owns everything gateway-side. Resources it emits:

| File | Resource | Role |
|---|---|---|
| `envoy-gateway-application.yaml` | child ArgoCD `Application` | Installs the upstream `gateway-helm` chart into `envoy-gateway-system` (controller + CRDs). |
| `envoy-gatewayclass.yaml` | `GatewayClass envoy-gateway` | Names the controller. |
| `envoy-proxy-config.yaml` | `EnvoyProxy` | Customises the Envoy data-plane Service (`externalTrafficPolicy: Local`). |
| `envoy-gateway.yaml` | `Gateway envoy-gateway` | Listeners: `web` (HTTP 80), `websecure` + `websecure-apex` (HTTPS 443, LAN-only), `websecure-public` + `websecure-public-apex` (HTTPS 4443, Cloudflare-only, conditional on `cloudflareOriginCA`), plus per-env extras (sftpgo 2222/TCP, ftp 2121/TCP, dns 53/UDP, samba 445/TCP). `spec.addresses` pins the LoadBalancer IP. |
| `envoy-client-traffic-policy.yaml` | `ClientTrafficPolicy envoy-gateway-client-ip-detection` | Gateway-wide `clientIPDetection.customHeader: CF-Connecting-IP`. |
| `envoy-mtls-client-traffic-policy.yaml` | `ClientTrafficPolicy envoy-gateway-mtls` | Listener-scoped (`sectionName: websecure-public` + `…-apex`). Requires mTLS via `tls.clientValidation.caCertificateRefs → cloudflare-origin-ca` ConfigMap. Re-emits `clientIPDetection` because Envoy Gateway uses full-replace precedence between Gateway-level and sectionName-level CTPs — omitting it would lose CF-Connecting-IP detection for these listeners. Conditional on `cloudflareOriginCA`. |
| `cloudflare-origin-ca-configmap.yaml` | `ConfigMap cloudflare-origin-ca` | Holds the CA certificate PEM. The matching leaf is uploaded to Cloudflare. Conditional on `cloudflareOriginCA`. |
| `wildcard-certificate.yaml` | cert-manager `Certificate` | Wildcard `*.<domain>` + apex `<domain>` cert (issuer: `letsencrypt-production`). |
| `oidc-client-secret-external-secret.yaml` | `ExternalSecret` → `Secret oidc-client-secret` | Pulls the Cognito client secret from Akeyless. |
| `oidc-security-policy.yaml` | **`SecurityPolicy gateway-oidc`** | The Gateway-level SP. See below. |
| `auth-callback-httproute.yaml` | `HTTPRoute auth-callback` + `HTTPRouteFilter auth-callback-sink` | Routes `auth.<domain>` (on both `websecure` and `websecure-public`) to a `directResponse: 200 "OK"` sink. The OAuth2 filter intercepts `/oauth2/callback` and `/oauth2/logout` on this host inline; the sink is just so the listener has a route to attach to. |
| `strip-oauth-cookies.yaml` | **`EnvoyExtensionPolicy strip-oauth-cookies`** | Gateway-level Lua filter. See below. |

#### `gateway-oidc` (the heart of the setup)

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: gateway-oidc
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: envoy-gateway
  oidc:
    provider:
      issuer: <Cognito user pool URL>
    clientID: <Cognito app client ID>
    clientSecret:
      name: oidc-client-secret   # in the same namespace as the Gateway
    redirectURL: https://auth.<domain>/oauth2/callback
    logoutPath: /oauth2/logout
    scopes: [openid, email]
    cookieDomain: <domain>        # e.g. "hess.pm"
  # no `authorization:` block — access control is at the listener layer
```

The OIDC block makes Envoy's native OAuth2 HTTP filter handle the entire OIDC dance — redirect-to-IDP, callback parsing, token cookies, refresh, logout. Because it's attached to the Gateway (not a route), **one** HMAC is used to sign all session cookies, and `cookieDomain` makes them visible across every `*.<domain>` subdomain. That's the SSO mechanism.

There is no `authorization` block. We rely entirely on the **listener layer** for who-gets-in:

- **LAN listener (443)** is reachable only because MetalLB L2-announces the LB IP on the LAN VLAN and the router doesn't NAT WAN traffic to it.
- **Cloudflare listener (4443)** rejects any TLS handshake that doesn't present a Cloudflare-signed client cert.

#### `strip-oauth-cookies`

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyExtensionPolicy
metadata:
  name: strip-oauth-cookies
spec:
  targetRefs:
    - kind: Gateway
      name: envoy-gateway
  lua:
    - type: Inline
      inline: |
        local STRIP = { IdToken=true, AccessToken=true, RefreshToken=true,
                        BearerToken=true, OauthHMAC=true, OauthExpires=true,
                        OauthNonce=true }
        -- ...walk Cookie header, drop STRIP entries, rebuild...
```

After Envoy validates the OAuth2 cookies on the way in, this filter strips them from the `Cookie` header before the request leaves Envoy for the upstream. Pods never see them, so they don't blow Node's 16 KB / Tomcat's ~8 KB header limits. Without this, `n8n`, `immich`, `stirling-pdf` and similar would return HTTP 431 every time.

The filter only mutates the request path, doesn't depend on inter-phase state or filter ordering, and is safe to upgrade Envoy under.

#### Cloudflare AOP listener (port 4443)

The piece that gates public traffic. Three moving parts must line up:

1. **CA + leaf cert pair** generated locally:
   ```bash
   openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
     -subj "/CN=homelab-aop-ca" -keyout ca.key -out ca.crt
   openssl req -new -newkey rsa:2048 -nodes \
     -subj "/CN=cloudflare-origin" -keyout leaf.key -out leaf.csr
   openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
     -days 3650 -out leaf.crt \
     -extfile <(printf "extendedKeyUsage=clientAuth")
   ```
2. **`ca.crt`** goes into `global.security.cloudflareOriginCA` (per-env values), rendered into the `cloudflare-origin-ca` ConfigMap, referenced by the mTLS `ClientTrafficPolicy`.
3. **`leaf.crt` + `leaf.key`** uploaded to Cloudflare → SSL/TLS → Origin Server → Authenticated Origin Pulls → **Per-Hostname** (or zone-level if you prefer). The hostname rule must be **enabled** for every protected hostname (`*.<domain>`).

Note: Cloudflare's UI labels the uploaded cert's "Subject" field as the *issuer DN*, not the leaf subject. Don't be confused — verify with `openssl x509 -in leaf.crt -noout -subject` locally that you uploaded the leaf, not the CA.

### `apps/generic/templates/security-policy.yaml`

Emits a **route-level** `SecurityPolicy` **only when a route opts out of OIDC**. Trigger: `oauth: false` (or `oauth` absent) on the `ingress.https[]` entry. Body is just `authorization: { defaultAction: Allow }`.

```yaml
# Excerpt — per-route opt-out emission
{{- if not $ingress.oauth }}
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
spec:
  targetRefs:
    - kind: HTTPRoute
      name: <appname>-https-<port>-<index>
  authorization:
    defaultAction: Allow
{{- end }}
```

This works because Envoy Gateway uses **full-replace precedence**: a route-level SP completely replaces the Gateway-level SP for the routes it targets. The body has to contain *something* valid (SP requires at least one policy block), so we use a no-op `authorization: { defaultAction: Allow }`. The net effect is: OIDC dropped for this route, no IP filter, listener-level gates still apply.

Routes with `oauth: true` (or no override needed) emit **nothing** here and inherit the Gateway-level SP intact (OIDC enforced).

### `apps/generic/templates/httproute.yaml`

Each route emits an HTTPRoute with **dual `parentRefs`** when `global.security.cloudflareOriginCA` is set:

```yaml
parentRefs:
  - name: envoy-gateway
    namespace: argocd
    sectionName: websecure          # LAN listener
  - name: envoy-gateway              # only emitted when cloudflareOriginCA is set
    namespace: argocd
    sectionName: websecure-public    # Cloudflare listener
```

Same Route logic, attached to both listeners. Whichever listener the request arrives on, the matching rule + backend + filters apply.

### `apps/argocd/templates/argocd-security-policy.yaml`

ArgoCD doesn't use the generic chart (it has bespoke HTTPRoutes). Same opt-out pattern hand-applied:

- `argocd-https-main` (the UI) — no per-route SP → inherits Gateway-level → OIDC.
- `argocd-https-webhook` (GitHub webhooks, etc.) — per-route SP `argocd-securitypolicy-webhook` emits `authorization: { defaultAction: Allow }`, dropping OIDC. The webhook reaches argocd either via Cloudflare (mTLS-gated) or LAN (LAN-gated) — either way the listener has already vetted the caller.

## Request flow

### Authenticated user hitting a protected route (`https://argocd.hess.pm/`)

**From LAN:**
1. DNS resolves to the LB IP. Browser connects to `:443` (LAN listener).
2. TLS terminated at the Gateway (wildcard cert, no mTLS on this listener).
3. `HTTPRoute argocd-https-main` matches. No per-route SP attached.
4. Envoy's OAuth2 filter (inherited from `gateway-oidc`) reads the `IdToken` cookie, validates HMAC + expiry, decodes the claims.
5. Filter sets `X-Auth-Request-User` / `X-Auth-Request-Email` headers on the upstream request.
6. `strip-oauth-cookies` Lua strips `IdToken`, `AccessToken`, `OauthHMAC`, etc. from the `Cookie` header.
7. Forwarded to `argocd-server-tls` Backend.

**From the public internet:**
1. DNS resolves to Cloudflare. Browser → Cloudflare edge.
2. Cloudflare's WAF runs.
3. Cloudflare opens a TLS connection to the origin's WAN IP on port 443. The router NATs to `:4443` (Cloudflare listener).
4. Cloudflare presents the AOP leaf cert. Envoy validates against `cloudflare-origin-ca`. Handshake succeeds.
5. `HTTPRoute argocd-https-main` matches (attached to both listeners via dual `parentRefs`).
6. Steps 4–7 from the LAN flow above.

### Unauthenticated user hitting a protected route

1. OAuth2 filter sees no valid `IdToken` cookie.
2. Filter generates a `302` to Cognito's authorization endpoint with the original URL encoded into the OAuth2 `state` parameter.
3. Browser → Cognito hosted UI → user signs in.
4. Cognito redirects browser to `https://auth.<domain>/oauth2/callback?code=...&state=...`.
5. The `auth-callback` HTTPRoute is attached to the same Gateway listeners, so the same OAuth2 filter intercepts `/oauth2/callback` before the route's `directResponse` sink ever sees it.
6. Filter exchanges the code for tokens at Cognito's token endpoint, sets `IdToken` + sibling cookies on the response with `Domain=<domain>` (so they're visible on every subdomain).
7. Filter reads the original URL from `state` and emits a `302` back to it.
8. Browser → original URL again, now with cookies → flow falls through to "authenticated" above.

### Opt-out route (`https://argocd.hess.pm/api/webhook`)

1. `HTTPRoute argocd-https-webhook` matches. Per-route SP `argocd-securitypolicy-webhook` is attached.
2. Per full-replace precedence, the route-level SP replaces `gateway-oidc` for this route — no OIDC check.
3. `defaultAction: Allow` admits the request. Listener gating (LAN topology or Cloudflare mTLS) is the only check.
4. Forwarded to `argocd-server-tls` Backend, no auth headers added.

### Sign-out

`logoutPath: /oauth2/logout` is configured on the Gateway-level SP. Hitting `https://<any-subdomain>.<domain>/oauth2/logout` clears the cookies (set with `Domain=<domain>`, so the clear applies everywhere).

## Per-route `oauth` flag

The only auth-related knob exposed in app values:

| Value | Effect on the route |
|---|---|
| `oauth: true` | (Default for catch-all routes.) Route inherits Gateway-level SP → OIDC enforced. **No** per-route SP is generated. |
| `oauth: false` *or* absent | Route-level SP overrides Gateway-level → OIDC dropped, request admitted by listener layer only. Used for `/api` paths, webhooks, audiobookshelf, etc. |

There is no `public` tier — every route is constrained by the listener it's attached to. If a route must be reachable from the public internet, the only path is through Cloudflare (mTLS-gated) or LAN. There's no "anywhere on the internet, no gating" option.

## Per-env config (`homelab-environments/<env>/values.yaml`)

The bits that vary per environment:

```yaml
global:
  oidc:
    issuerUrl: "https://cognito-idp.<region>.amazonaws.com/<userpool-id>"
    clientId:  "<Cognito app client id>"
    cookieDomain: "<env-domain>"          # NOTE: no RFC 6265 leading dot — Envoy rejects it
  security:
    cloudflareOriginCA: |                  # the CA cert whose key signed the leaf uploaded to Cloudflare AOP
      -----BEGIN CERTIFICATE-----
      MIID...
      -----END CERTIFICATE-----

apps:
  envoy-gateway:
    enabled: true
    argocd:
      helm:
        values:
          loadBalancerIP: 192.168.1.80    # pinned external IP for the Gateway Service
          additionalExposedPorts:         # extra listeners on the Gateway
            plex: { port: 32400, protocol: TCP }
            dns:  null                     # set null to remove a default-chart listener
```

Notes:
- `global.security.lanCIDRs` was removed from `base-chart/values.yaml` and `apps/generic/values.schema.json` — it became unreferenced after the listener-split refactor. Drop it from env values too; it is silently ignored if left behind.
- If `cloudflareOriginCA` is unset, the public listener pair, the mTLS `ClientTrafficPolicy`, and the public `parentRef` on every HTTPRoute are all skipped — the cluster runs LAN-only. Useful for local Kind clusters.

## Router / Cloudflare setup

- **Router**: NAT `WAN:443 → <internal LB IP>:4443`. Leave `WAN:443 → :443` *unmapped*. The split is what makes the LAN listener LAN-only.
- **Cloudflare**: SSL/TLS → Origin Server → Authenticated Origin Pulls → upload the **leaf** cert+key, enable for every protected hostname. The custom-cert AOP feature is what makes the listener safe to expose — without it, any Cloudflare zone could reach the origin.
- **DNS**: the hostname must be proxied (orange-cloud) on Cloudflare. Grey-cloud bypasses mTLS and will fail the handshake at the origin.

## Setting up Cloudflare mTLS for a new environment

Follow this end-to-end the first time you enable the public listener pair in a new environment (e.g. `home-server.dev`, `unsereiner.net`). Skip steps that don't apply if a piece already exists (a shared cert pair across envs is fine — see "Reusing a cert pair across environments" at the end).

### Prerequisites

- The env already has Envoy Gateway running with the LAN listener pair working (DNS resolves on LAN, browser reaches `https://<app>.<env-domain>` via `:443`).
- You control the Cloudflare zone for `<env-domain>` and have access to **SSL/TLS → Origin Server → Authenticated Origin Pulls** in the dashboard (Pro plan or higher for custom-cert per-hostname AOP).
- You can edit your router's port-forward / NAT rules.
- `openssl` available locally.

### Step 1 — Generate the CA + leaf cert pair

Pick a scratch dir on your local machine; it's a one-time generation but the **CA private key must survive** for future leaf rotation:

```bash
mkdir -p ~/cloudflare-aop/<env-name> && cd ~/cloudflare-aop/<env-name>

# 10-year CA. Keep ca.key offline (1Password, USB drive, ...).
openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
  -subj "/CN=<env-name>-aop-ca" \
  -keyout ca.key -out ca.crt

# 10-year leaf signed by the CA. Has clientAuth EKU (required for mTLS client cert).
openssl req -new -newkey rsa:2048 -nodes \
  -subj "/CN=cloudflare-origin" \
  -keyout leaf.key -out leaf.csr
openssl x509 -req -in leaf.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 3650 -out leaf.crt \
  -extfile <(printf "extendedKeyUsage=clientAuth")

# Sanity-check what you generated.
openssl x509 -in ca.crt   -noout -subject -issuer -dates
openssl x509 -in leaf.crt -noout -subject -issuer -dates -ext extendedKeyUsage
openssl verify -CAfile ca.crt leaf.crt   # should print "leaf.crt: OK"
```

You should now have `ca.crt`, `ca.key`, `leaf.crt`, `leaf.key`. **The cluster only ever holds `ca.crt`**; the leaf pair leaves your machine only once (uploaded to Cloudflare).

### Step 2 — Upload the leaf to Cloudflare AOP

1. Cloudflare dashboard → your zone → **SSL/TLS → Origin Server → Authenticated Origin Pulls**.
2. Switch to the **Per-Hostname** tab (zone-level works too, but per-hostname is finer-grained).
3. **Upload Certificate**. In the form:
   - Certificate: paste the contents of `leaf.crt`.
   - Private Key: paste the contents of `leaf.key`.
   - Click upload.
4. Cloudflare's UI confusingly labels the uploaded cert's "Certificate subject" field as the *issuer DN* (`CN=<env-name>-aop-ca`). Don't panic — that's the CA, not the leaf. Verify locally with `openssl x509 -in leaf.crt -noout -subject` that you uploaded the right file (subject should be `CN=cloudflare-origin`).
5. **Enable** the cert for every hostname Cloudflare will proxy to this origin. For a wildcard, enable it for each subdomain individually (Cloudflare doesn't have a single "enable for `*.<domain>`" toggle in per-hostname mode). At a minimum: `auth.<env-domain>` plus every app subdomain (`argocd`, `homer`, ...).
   - Quicker alternative: switch the toggle at **zone level** to "On" instead of per-hostname. Trade-off: applies to every hostname in the zone, including any you don't want mTLS on.

### Step 3 — Configure the router

NAT/port-forward `WAN:443 → <Gateway LB IP>:4443`.

- Do **not** also map `WAN:443 → :443`. The LAN listener (port 443) must remain unreachable from the WAN — that's the whole point of the split.
- If your router has multiple WAN interfaces or runs an upstream router (double-NAT), make sure the rule is in place on every hop. Example real-world chain: `WAN:443 → Router1:192.168.0.X:4443 → Router2:192.168.1.80:4443`.
- The internal LB IP is `apps.envoy-gateway.argocd.helm.values.loadBalancerIP` in your env values.
- **FRITZ!Box gotcha:** this needs the external port (443) to differ from the internal port (4443). Use a plain **Portfreigabe** (*Andere Anwendung*, TCP, *Port an Gerät* `4443`, *Port extern gewünscht* `443`). Do **not** use a **MyFRITZ!-Freigabe** — it greys out the external-port field and forces external = internal, so it can only open `WAN:4443` (Cloudflare connects on 443 → HTTP 523).

Symptom decoding: Cloudflare **523** = `WAN:443` never reaches Envoy (router/port-forward wrong). **525** = TCP path works but the mTLS handshake failed (AOP cert wrong or not enabled).

Verify from outside the LAN (mobile tether) with `openssl s_client`:

```bash
# Should fail with "tlsv13 alert certificate required" or similar — that's expected,
# you're connecting without a client cert.
openssl s_client -connect <env-domain>:443 -servername <app>.<env-domain> </dev/null
```

A clean TLS handshake error proves: the port reaches Envoy, Envoy demands a client cert, you don't have one. Good.

### Step 4 — Push the CA to the cluster

In `homelab-environments/<env>/values.yaml`:

```yaml
global:
  security:
    cloudflareOriginCA: |
      -----BEGIN CERTIFICATE-----
      <contents of ca.crt>
      -----END CERTIFICATE-----
```

Commit + push. ArgoCD syncs `apps/envoy-gateway` and emits:
- `cloudflare-origin-ca` ConfigMap (Step 1's `ca.crt`).
- `websecure-public` + `websecure-public-apex` listeners on the Gateway (port 4443).
- `envoy-gateway-mtls` ClientTrafficPolicy with `tls.clientValidation.caCertificateRefs → cloudflare-origin-ca`.
- Every HTTPRoute gets a second `parentRef` pointing at `websecure-public`.

Verify:

```bash
kubectl get configmap -n argocd cloudflare-origin-ca -o jsonpath='{.data}' | head -c 80
kubectl get clienttrafficpolicy -n argocd envoy-gateway-mtls -o yaml \
  | yq '.status.ancestors[].conditions[] | {type, status, message}'
kubectl get gateway -n argocd envoy-gateway -o yaml \
  | yq '.spec.listeners[] | {name, port, protocol, hostname}'
# Expect: web/80, websecure/443, websecure-apex/443, websecure-public/4443, websecure-public-apex/4443
```

### Step 5 — DNS

Every hostname that should be reachable via Cloudflare must be **proxied (orange-cloud)** in Cloudflare DNS, pointing at the WAN IP. Grey-cloud bypasses Cloudflare and the mTLS handshake will fail at the origin.

### Step 6 — Smoke test

```bash
# From an external network (mobile tether), with curl following redirects:
curl -v https://<app>.<env-domain>/

# Expect: TLS handshake completes (via Cloudflare → AOP → origin), then either:
#  - 302 to Cognito (if the app uses oauth: true) — auth flow works
#  - 200 (if the app is oauth: false) — direct response
```

Cross-check the access log to confirm the request actually hit the public listener:

```bash
kubectl logs -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=envoy-gateway \
  --tail=20 | jq -r '"\(.response_code) port=\(.downstream_local_address) \(.\":authority\")"'
# Expect a line with port=...:4443  (Envoy binds the :4443 Cloudflare listener
# directly — non-privileged port, no offset. The :443 LAN listener, being
# privileged, is the one that maps to a per-pod :10443.)
```

### Reusing a cert pair across environments

If multiple envs share an operator and you don't need cryptographic isolation between them, you can reuse the same CA + leaf pair. Just paste the same `ca.crt` PEM into each env's `global.security.cloudflareOriginCA` and upload the same leaf to each Cloudflare zone. Trade-off: a compromise of the CA key compromises every env. Acceptable for homelab; not for production multi-tenant setups.

### Rotation

Two situations:

- **Routine rotation (leaf)** — generate a new leaf signed by the *same* CA, upload to Cloudflare. The CA in the cluster validates both old and new leaves automatically. No cluster restart, no values change.
- **CA rotation** — generate a new CA + leaf. Concat both old and new CA PEMs in `cloudflareOriginCA` (Envoy accepts a bundle). Upload the new leaf to Cloudflare alongside the old. Once Cloudflare is serving the new leaf, remove the old CA from values and the old leaf from Cloudflare.

## Akeyless / secrets

- **`/<env>/oidc/oauth2-proxy/client_secret`** — Cognito app client secret. Pulled by `oidc-client-secret-external-secret.yaml` into the `Secret oidc-client-secret` in the Gateway namespace. The OAuth2 filter reads `clientSecret` from that Secret. (Path name is historical — we used to run oauth2-proxy.)
- The Cognito **client ID** is non-secret and lives in `global.oidc.clientId`.
- The AOP **CA certificate** (`cloudflareOriginCA`) is a public cert (no key) — fine to keep in env values plaintext. The **CA key** stays on the operator's machine; only the leaf cert+key is given to Cloudflare. Nothing on the cluster ever holds the CA key.

## Cognito setup

For each environment's Cognito app client:

- **Callback URLs**: exactly one — `https://auth.<env-domain>/oauth2/callback`.
- **Sign-out URLs**: `https://<any URL on a hess.pm subdomain>` (Cognito requires at least one).
- **Allowed OAuth scopes**: `openid`, `email`. (No `profile` — kept off to reduce ID-token size and therefore cookie size.)
- **Allowed OAuth flows**: Authorization code grant.

## Adding a new app

Default — full OIDC, reachable from LAN and (if `cloudflareOriginCA` is set) Cloudflare — needs no auth-related fields:

```yaml
generic:
  ingress:
    https:
      - subdomain: <appname>
        port: <port>
        oauth: true     # explicit, but redundant — same as the default for a catch-all route
```

For an app that exposes an unauthenticated API path:

```yaml
generic:
  ingress:
    https:
      - subdomain: <appname>          # main UI route — OIDC-protected
        port: <port>
        oauth: true
      - subdomain: <appname>          # API route — no OIDC
        port: <port>
        pathPrefixes: [/api]
        # oauth: false  (omitted == false)
```

Order matters in `ingress.https[]`: Gateway API picks the most-specific path match, so the path-prefix entry catches `/api/*` before the catch-all.

## Debugging cheatsheet

```bash
# Is the Gateway-level SP accepted?
kubectl get securitypolicy -n argocd gateway-oidc -o yaml \
  | yq '.status.ancestors[].conditions[]'

# Which routes are overriding it? (Expected: every route with oauth: false.)
kubectl get securitypolicy -n argocd gateway-oidc -o jsonpath='{.status.ancestors[*].conditions[*].message}'

# Was a per-route SP accepted?
kubectl get securitypolicy -n argocd <appname>-securitypolicy-<port>-<index> -o yaml \
  | yq '.status.ancestors[].conditions[]'

# Is the mTLS ClientTrafficPolicy accepted?
kubectl get clienttrafficpolicy -n argocd envoy-gateway-mtls -o yaml \
  | yq '.status.ancestors[].conditions[]'

# Is the CA ConfigMap present?
kubectl get configmap -n argocd cloudflare-origin-ca -o yaml

# Live access log (one line per request) — useful to verify which listener served a request
kubectl logs -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=envoy-gateway \
  --tail=20 -f | jq -r '"\(.response_code) port=\(.downstream_local_address) \(.\":authority\") \(.\"x-envoy-origin-path\")"'

# Envoy controller logs (xDS push, policy translation errors)
kubectl logs -n envoy-gateway-system deploy/envoy-gateway --tail=200 | grep -iE "oidc|securitypolicy|clienttrafficpolicy|error|warn"

# Is the OIDC HMAC secret present?
kubectl get secret -n envoy-gateway-system envoy-oidc-hmac

# Is the Cognito client secret synced from Akeyless?
kubectl get secret -n argocd oidc-client-secret -o jsonpath='{.data.client-secret}' | base64 -d | wc -c
```

Common failure modes:

| Symptom | Likely cause |
|---|---|
| `RBAC: access denied` on a public route | Stale browser state from the previous LAN-only `authorization` rules; the per-route SP since changed to `defaultAction: Allow`. Clear cookies / try incognito. If it still reproduces, check the per-route SP for a stray old `authorization` block. |
| Cloudflare 502/525/526 on a public route | mTLS handshake failed. Either AOP isn't enabled for that hostname in Cloudflare, the leaf cert in Cloudflare doesn't match the CA in `cloudflareOriginCA`, or the router isn't NATting WAN:443 to the LB's :4443. |
| Public route returns 404 but LAN works | The HTTPRoute isn't attached to `websecure-public`. Verify `parentRefs` has both `sectionName: websecure` and `sectionName: websecure-public`. |
| Every protected route lets you in with no login | A per-route SP is overriding `gateway-oidc` unintentionally. Check the `gateway-oidc` status message for "being overridden by..." — anything in that list that *shouldn't* be there has a stray `oauth: false`. |
| Browser is stuck in a redirect loop between app subdomain and Cognito | `cookieDomain` is wrong (e.g. has the leading dot — Envoy's schema rejects `.hess.pm`, only accepts `hess.pm`). |
| HTTP 431 from a Node/Tomcat upstream | `strip-oauth-cookies` ExtensionPolicy is missing or not Accepted. `kubectl get envoyextensionpolicy -n argocd strip-oauth-cookies`. |
| `OIDC config not found` in controller logs | `oidc-client-secret` Secret missing or has the wrong key (must be `client-secret`). |

## Caveats / future work

- **Origin IP discoverability**: the WAN IP still answers on TCP/443 (Cloudflare's source), but the mTLS handshake rejects anyone without a Cloudflare-signed cert before any HTTP is exchanged. Port scanners see "TLS bad cert" responses, not application data. If you want zero TCP exposure, Cloudflare Tunnel is the bigger move (origin becomes outbound-only).
- **CA / leaf rotation**: cert pair is currently 10-year-validity. Rotate before expiry by generating a new pair, uploading the new leaf to Cloudflare alongside the old one, updating `cloudflareOriginCA` in env values to include both PEMs concatenated (Envoy accepts a bundle), waiting for sync, then removing the old leaf from Cloudflare and the old CA from values.
- **`global.security.lanCIDRs`** has been removed from base-chart and `apps/generic/values.schema.json` (it was unreferenced after the listener-split refactor). Remove it from any env values that still set it.
- **No `public-no-auth` tier**: every route requires *something* — either OIDC or listener-level gating. To make a route truly anonymous, it would need its own listener with no SP. Not done today.
- **Cookie size sensitivity**: `scopes` are `[openid, email]`. Adding `profile` would add ~1–2 KB to the ID-token cookie and could push some Tomcat-based upstreams back over their header limit (currently masked by the cookie-strip filter, but margin for safety).

## Files

| Path | Role |
|---|---|
| `apps/envoy-gateway/templates/envoy-gateway.yaml` | Gateway resource + listeners (port 443 LAN, port 4443 public) |
| `apps/envoy-gateway/templates/envoy-client-traffic-policy.yaml` | Gateway-wide `CF-Connecting-IP` detection |
| `apps/envoy-gateway/templates/envoy-mtls-client-traffic-policy.yaml` | mTLS validation on the public listener pair |
| `apps/envoy-gateway/templates/cloudflare-origin-ca-configmap.yaml` | Holds the AOP CA cert |
| `apps/envoy-gateway/templates/oidc-security-policy.yaml` | Gateway-level SP (OIDC only) |
| `apps/envoy-gateway/templates/strip-oauth-cookies.yaml` | Gateway-level Lua to strip auth cookies before upstream |
| `apps/envoy-gateway/templates/auth-callback-httproute.yaml` | HTTPRoute + sink for `auth.<domain>` (dual parentRefs) |
| `apps/envoy-gateway/templates/oidc-client-secret-external-secret.yaml` | Akeyless → `Secret oidc-client-secret` |
| `apps/envoy-gateway/templates/envoy-gateway-application.yaml` | ArgoCD child app installing upstream `gateway-helm` |
| `apps/generic/templates/httproute.yaml` | Dual-parentRef HTTPRoutes |
| `apps/generic/templates/security-policy.yaml` | Per-route SP override (only emitted for `oauth: false`) |
| `apps/generic/values.schema.json` | Validates the `oauth` field and `global.security.cloudflareOriginCA` |
| `apps/argocd/templates/argocd-httproute.yaml` | ArgoCD's dual-parentRef HTTPRoutes |
| `apps/argocd/templates/argocd-security-policy.yaml` | Per-route SP for `argocd-https-webhook` (opt-out) |
| `homelab-environments/<env>/values.yaml` | Per-env `global.oidc.*`, `global.security.cloudflareOriginCA`, `apps.envoy-gateway.argocd.helm.values` |

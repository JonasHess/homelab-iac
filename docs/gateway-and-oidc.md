# Gateway, OIDC, and auth

How HTTPS traffic is terminated, authenticated and authorised in this homelab.

## TL;DR

- **Envoy Gateway** (upstream `oci://docker.io/envoyproxy/gateway-helm` v1.7.2) is the Gateway-API controller. Installed by `apps/envoy-gateway/`.
- One `Gateway` resource, `envoy-gateway` in the `argocd` namespace, terminates TLS for `*.<domain>` and `<domain>` using a wildcard cert managed by cert-manager.
- One **Gateway-level `SecurityPolicy`** (`gateway-oidc`) attaches **two** policies to that Gateway: native Envoy OIDC against AWS Cognito, and an IP allowlist (`cloudflareCIDRs`). Every route inherits both by default.
- Per-route `SecurityPolicy` resources are emitted **only to opt a route out of OIDC** (e.g. `/api`, webhooks, public routes). They carry only the IP allowlist; per Envoy Gateway's full-replace precedence, that removes OIDC for the route while keeping the allowlist.
- A Gateway-level **`EnvoyExtensionPolicy`** (`strip-oauth-cookies`) runs a Lua filter that strips Envoy's encrypted OIDC cookies from the request before it reaches the upstream pod, so Node.js (16 KB) and Tomcat (~8 KB) header limits never see them.
- One Cognito callback URL: `https://auth.<domain>/oauth2/callback`. Cookies are scoped to the parent domain (`cookieDomain: <domain>`), so one Cognito sign-in covers every subdomain.

## Components

### `apps/envoy-gateway/`

The chart that owns everything gateway-side. Resources it emits:

| File | Resource | Role |
|---|---|---|
| `envoy-gateway-application.yaml` | child ArgoCD `Application` | Installs the upstream `gateway-helm` chart into `envoy-gateway-system` (controller + CRDs). |
| `envoy-gatewayclass.yaml` | `GatewayClass envoy-gateway` | Names the controller. |
| `envoy-proxy-config.yaml` | `EnvoyProxy` | Customises the Envoy data-plane Service (`externalTrafficPolicy: Local`). |
| `envoy-gateway.yaml` | `Gateway envoy-gateway` | The actual Gateway resource. Listeners: `web` (HTTP 80), `websecure` (HTTPS 443, `*.<domain>`), `websecure-apex` (HTTPS 443, `<domain>`), plus per-env extras (sftpgo 2222/TCP, ftp 2121/TCP, dns 53/UDP, samba 445/TCP). `spec.addresses` pins the LoadBalancer IP. |
| `envoy-client-traffic-policy.yaml` | `ClientTrafficPolicy` | `clientIPDetection.xForwardedFor.numTrustedHops: 1` so Envoy treats the first proxy hop as trusted for real-client-IP detection. |
| `wildcard-certificate.yaml` | cert-manager `Certificate` | Wildcard `*.<domain>` + apex `<domain>` cert (issuer: `letsencrypt-production`). |
| `oidc-client-secret-external-secret.yaml` | `ExternalSecret` → `Secret oidc-client-secret` | Pulls the Cognito client secret from Akeyless. |
| `oidc-security-policy.yaml` | **`SecurityPolicy gateway-oidc`** | The Gateway-level SP. See below. |
| `auth-callback-httproute.yaml` | `HTTPRoute auth-callback` + `HTTPRouteFilter auth-callback-sink` | Routes `auth.<domain>` to a `directResponse: 200 "OK"` sink. The OAuth2 filter intercepts `/oauth2/callback` and `/oauth2/logout` on this host inline; the sink is just so the listener has a route to attach to. |
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
  authorization:
    defaultAction: Deny
    rules:
      - action: Allow
        principal:
          clientCIDRs:
            - <cidrs from global.security.cloudflareCIDRs>
```

The OIDC block makes Envoy's native OAuth2 HTTP filter handle the entire OIDC dance — redirect-to-IDP, callback parsing, token cookies, refresh, logout. Because it's attached to the Gateway (not a route), **one** HMAC is used to sign all session cookies, and `cookieDomain` makes them visible across every `*.<domain>` subdomain. That's the SSO mechanism.

The authorization block is the IP allowlist. Mandatory everywhere — there is no "public" tier in this setup.

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

### `apps/generic/templates/security-policy.yaml`

Emits a **route-level** `SecurityPolicy` **only when a route opts out of OIDC**. Trigger: `oauth: false` (or `oauth` absent) on the `ingress.https[]` entry. Body: only `authorization` (cloudflareCIDRs).

This works because Envoy Gateway uses **full-replace precedence**: a route-level SP completely replaces the Gateway-level SP for that route. Omitting `oidc:` in the route-level SP therefore *removes* OIDC enforcement for that route while preserving the mandatory IP allowlist.

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
    defaultAction: Deny
    rules:
      - action: Allow
        principal:
          clientCIDRs: [<cloudflareCIDRs>]
{{- end }}
```

Routes with `oauth: true` (or no override needed) emit **nothing** here and inherit the Gateway-level SP intact.

### `apps/argocd/templates/argocd-security-policy.yaml`

ArgoCD doesn't use the generic chart (it has bespoke HTTPRoutes). The same opt-out pattern is hand-applied:

- `argocd-https-main` (the UI) — no per-route SP → inherits Gateway-level → OIDC + allowlist.
- `argocd-https-webhook` (GitHub webhooks, etc.) — per-route SP `argocd-securitypolicy-webhook` with `authorization` only → no OIDC, allowlist only.

## Request flow

### Authenticated user hitting a protected route (`https://argocd.hess.pm/`)

1. TLS terminated at the Gateway (wildcard cert).
2. `HTTPRoute argocd-https-main` matches. No per-route SP attached.
3. Envoy's OAuth2 filter (inherited from `gateway-oidc`) reads the `IdToken` cookie, validates HMAC + expiry, decodes the claims.
4. Filter sets `X-Auth-Request-User` / `X-Auth-Request-Email` headers on the upstream request.
5. `strip-oauth-cookies` Lua strips `IdToken`, `AccessToken`, `OauthHMAC`, etc. from the `Cookie` header.
6. Envoy's authorization rule (also inherited) checks the client IP against `cloudflareCIDRs`. Allow.
7. Forwarded to `argocd-server-tls` Backend (with `insecureSkipVerify: true` against argocd-server's self-signed cert).

### Unauthenticated user hitting a protected route

1. OAuth2 filter sees no valid `IdToken` cookie.
2. Filter generates a `302` to Cognito's authorization endpoint with the original URL encoded into the OAuth2 `state` parameter.
3. Browser → Cognito hosted UI → user signs in.
4. Cognito redirects browser to `https://auth.hess.pm/oauth2/callback?code=...&state=...`.
5. That HTTPRoute (`auth-callback`) is attached to the same Gateway, so the same OAuth2 filter intercepts `/oauth2/callback` before the route's `directResponse` sink ever sees it.
6. Filter exchanges the code for tokens at Cognito's token endpoint, sets `IdToken` + sibling cookies on the response with `Domain=hess.pm` (so they're visible on every subdomain).
7. Filter reads the original URL from `state` and emits a `302` back to it.
8. Browser → `https://argocd.hess.pm/` again, now with cookies → flow falls through to "authenticated" above.

### User hitting an opt-out route (`https://argocd.hess.pm/api/webhook`)

1. `HTTPRoute argocd-https-webhook` matches. Per-route SP `argocd-securitypolicy-webhook` is attached.
2. Per full-replace precedence, the route-level SP replaces `gateway-oidc` for this route — only `authorization` is in effect.
3. No OIDC check. Source IP must be in `cloudflareCIDRs`; if so, allow.
4. Forwarded to `argocd-server-tls` Backend, no auth headers added.

### User signing out

`logoutPath: /oauth2/logout` is configured on the Gateway-level SP. Hitting `https://<any-subdomain>.hess.pm/oauth2/logout` clears the cookies (which were set with `Domain=hess.pm`, so the clear applies everywhere).

## Per-route `oauth` flag

The only auth-related knob exposed in app values:

| Value | Effect on the route |
|---|---|
| `oauth: true` | (Default for most catch-all routes.) Route inherits Gateway-level SP → OIDC + IP allowlist. **No** per-route SP is generated. |
| `oauth: false` *or* absent | Route-level SP overrides Gateway-level → IP allowlist only, **no** OIDC. Used for `/api` paths, webhooks, audiobookshelf, etc. |

There is no `public` tier — `cloudflareCIDRs` is mandatory at the Gateway level and re-included in every route-level override. If a route truly needs no auth and no IP filter, it would need to be moved to a separate Gateway listener without an attached SP (not currently done anywhere).

## Per-env config (`homelab-environments/<env>/values.yaml`)

The bits that vary per environment:

```yaml
global:
  oidc:
    issuerUrl: "https://cognito-idp.<region>.amazonaws.com/<userpool-id>"
    clientId:  "<Cognito app client id>"
    cookieDomain: "<env-domain>"          # NOTE: no RFC 6265 leading dot — Envoy rejects it
  security:
    cloudflareCIDRs: [ <CIDRs that are allowed in> ]
    lanCIDRs: []                          # unused today; reserved for future per-route cloudflare-and-lan

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

## Akeyless / secrets

- **`/<env>/oidc/oauth2-proxy/client_secret`** — Cognito app client secret. Pulled by `oidc-client-secret-external-secret.yaml` into the `Secret oidc-client-secret` in the Gateway namespace. The OAuth2 filter reads `clientSecret` from that Secret. (The Akeyless path is still called `oauth2-proxy/client_secret` for historical reasons — we used to run oauth2-proxy. Nothing else lives at that path now.)

The Cognito **client ID** is non-secret and lives in `global.oidc.clientId` (env values, then base-chart fallback).

## Cognito setup

For each environment's Cognito app client:

- **Callback URLs**: exactly one — `https://auth.<env-domain>/oauth2/callback`.
- **Sign-out URLs**: `https://<any URL on a hess.pm subdomain>` (Cognito requires at least one; it doesn't have to be reached).
- **Allowed OAuth scopes**: `openid`, `email`. (No `profile` — kept off to reduce ID-token size and therefore cookie size.)
- **Allowed OAuth flows**: Authorization code grant.

When adding a new env, the only Cognito side change is registering its own `https://auth.<that-env>/oauth2/callback`.

## Adding a new app

The default — full OIDC, IP allowlist, no special handling — needs no auth-related fields. Existing pattern in app `values.yaml`:

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

# Live access log (one line per request)
kubectl logs -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=envoy-gateway \
  --tail=20 -f | jq -r '"\(.response_code) \(.response_code_details) \(.\":authority\") \(.\"x-envoy-origin-path\")"'

# Envoy controller logs (xDS push, policy translation errors)
kubectl logs -n envoy-gateway-system deploy/envoy-gateway --tail=200 | grep -iE "oidc|securitypolicy|error|warn"

# Is the OIDC HMAC secret present?
kubectl get secret -n envoy-gateway-system envoy-oidc-hmac

# Is the Cognito client secret synced from Akeyless?
kubectl get secret -n argocd oidc-client-secret -o jsonpath='{.data.client-secret}' | base64 -d | wc -c
```

Common failure modes:

| Symptom | Likely cause |
|---|---|
| Every protected route lets you in with no login | A per-route SP is overriding `gateway-oidc` and only emitting `authorization`. Check the `gateway-oidc` status message for "being overridden by..." — anything in that list that *shouldn't* be there has a stray `oauth: false`. |
| Browser is stuck in a redirect loop between app subdomain and Cognito | `cookieDomain` is wrong (e.g. has the leading dot — Envoy's schema rejects `.hess.pm`, only accepts `hess.pm`). |
| HTTP 431 from a Node/Tomcat upstream | `strip-oauth-cookies` ExtensionPolicy is missing or not Accepted. `kubectl get envoyextensionpolicy -n argocd strip-oauth-cookies`. |
| 500 / `ext_authz_denied` with empty body on every request | Stale config from the previous oauth2-proxy `extAuth` setup. `argocd app sync envoy-gateway --core`. |
| `OIDC config not found` in controller logs | `oidc-client-secret` Secret missing or has the wrong key (must be `client-secret`). |
| `Policy could not be applied` in `gateway-oidc` status | `global.oidc.issuerUrl` / `clientId` is unset for this env. |

## Caveats / future work

- **Webhooks from external services** (e.g. GitHub → argocd `/api/webhook`): the source IP must be inside `cloudflareCIDRs`. If GitHub's IPs aren't covered (today they're not, since Cloudflare-tunneled traffic from outside isn't part of the LAN allowlist), the webhook will be denied at the authorization step. Either keep webhooks LAN-only or expand `cloudflareCIDRs` per env.
- **No `public` tier**: every route gets the IP allowlist. To make a route truly public (e.g. for `audiobookshelf` shared with outside listeners), it would need to be moved to a separate Gateway listener with no SP attached. Not done today.
- **One `cloudflareCIDRs` for the whole Gateway**: `cloudflare-and-lan` and per-route IP-list variation aren't supported. The old `access` enum is gone. Adding it back would require either reintroducing per-route SPs that re-include the OIDC block (cookie-collision risk) or partitioning routes across multiple Gateway listeners.
- **Cookie size sensitivity**: we kept `scopes` at `[openid, email]`. Adding `profile` would add ~1–2 KB to the ID-token cookie and could push some Tomcat-based upstreams back over their header limit (currently masked by the cookie-strip filter, but margin for safety).

## Files

| Path | Role |
|---|---|
| `apps/envoy-gateway/templates/oidc-security-policy.yaml` | Gateway-level SP (oidc + authorization) |
| `apps/envoy-gateway/templates/strip-oauth-cookies.yaml` | Gateway-level Lua to strip auth cookies before upstream |
| `apps/envoy-gateway/templates/auth-callback-httproute.yaml` | HTTPRoute + sink for `auth.<domain>` |
| `apps/envoy-gateway/templates/oidc-client-secret-external-secret.yaml` | Akeyless → `Secret oidc-client-secret` |
| `apps/envoy-gateway/templates/envoy-gateway.yaml` | Gateway resource + listeners |
| `apps/envoy-gateway/templates/envoy-gateway-application.yaml` | ArgoCD child app installing upstream `gateway-helm` |
| `apps/generic/templates/security-policy.yaml` | Per-route SP override (only emitted for `oauth: false`) |
| `apps/generic/values.schema.json` | Validates the `oauth` field on each `ingress.https[]` |
| `apps/argocd/templates/argocd-security-policy.yaml` | Per-route SP for `argocd-https-webhook` (opt-out) |
| `homelab-environments/<env>/values.yaml` | Per-env `global.oidc.*`, `global.security.cloudflareCIDRs`, `apps.envoy-gateway.argocd.helm.values` |

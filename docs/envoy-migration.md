# Envoy Gateway Migration

This document describes the migration from Traefik to Envoy Gateway and what
each homelab environment needs to do to stay deployable.

## Status

| Environment        | Migrated | Notes                                            |
|--------------------|----------|--------------------------------------------------|
| `hess.pm`          | yes      | Reference implementation — copy this pattern.    |
| `home-server.dev`  | no       | Will fail to sync until migrated.                |
| `unsereiner.net`   | no       | Will fail to sync until migrated.                |

Until an environment is migrated it cannot pick up `main`: `apps/traefik/` is
gone, and `global.oauth2_proxy.*` has been removed from `base-chart`.

## What changed in the iac repo

### New
- `apps/envoy-gateway/` — installs the upstream `oci://docker.io/envoyproxy/gateway-helm`
  controller, the Gateway resource, the wildcard cert, a `ClientTrafficPolicy`
  for X-Forwarded-For trust, and an `ExternalSecret` that fetches the OIDC
  client secret from Akeyless.
- `apps/generic/templates/security-policy.yaml` — emits one Envoy
  `SecurityPolicy` per HTTPRoute, with `oidc` and/or `authorization` based
  on the per-route `oauth` and `access` flags.

### Removed
- `apps/traefik/` (Gateway, Middlewares, oauth2-proxy subchart, dashboard).
- `apps/argocd/templates/argocd-transport.yaml` (Traefik `ServersTransport`).
  Replaced by `argocd-server-backend.yaml` — an Envoy `Backend` CR with
  `insecureSkipVerify: true` so the Gateway can still talk to argocd-server's
  self-signed HTTPS endpoint.
- `apps/homelab-lib/` (helper chart was only useful while Traefik filters
  were emitted inline; not needed for SecurityPolicy).

### Changed
- `apps/generic/templates/httproute.yaml` no longer emits any `filters:` block.
  Auth and IP-allowlisting are now handled by the sibling SecurityPolicy.
- `apps/generic/values.schema.json` — added `global.oidc.{issuerUrl, clientId, cookieDomain}`
  and `global.security.{cloudflareCIDRs, lanCIDRs}`.
- `apps/argocd/templates/argocd-httproute.yaml` — split into two HTTPRoutes
  (`argocd-https-webhook` and `argocd-https-main`) so OIDC can be applied to
  the main route while the webhook stays unauthenticated. Both `backendRefs`
  point at the new `argocd-server-tls` Backend CR.
- `apps/argocd/templates/argocd-configmap.yaml`, `apps/immich/templates/immich.yaml`,
  `apps/sftpgo/templates/sftpgo-config.yaml` — references to
  `global.oauth2_proxy.*` swapped for `global.oidc.*`.
- `base-chart/values.yaml` — `global.gateway.name` defaults to `envoy-gateway`,
  `oauth2_proxy` global removed, `oidc` and `security` placeholders added,
  `traefik` app entry replaced with `envoy-gateway` (default disabled).

### Per-route flag semantics (unchanged from the earlier feature-flag PR)
Each `ingress.https[]` item supports two flags:

| Flag      | Type    | Default            | Effect                                                   |
|-----------|---------|--------------------|----------------------------------------------------------|
| `oauth`   | bool    | `false`            | When true: SecurityPolicy with `oidc` is attached.       |
| `access`  | string  | `"cloudflare-only"`| `cloudflare-only` / `cloudflare-and-lan` / `public`. Drives the SecurityPolicy `authorization.clientCIDRs` source list (`public` emits no SecurityPolicy when `oauth` is also unset). |

## Migrating an environment

The reference example is `homelab-environments/hess.pm/values.yaml`. Apply the
same edits to the env you're migrating.

### 1. Replace the `oauth2_proxy:` global

```yaml
# REMOVE
global:
  oauth2_proxy:
    oidc_client_id: "<id>"
    oidc_issuer_url: "<url>"
```

```yaml
# ADD
global:
  oidc:
    issuerUrl: "<same as oidc_issuer_url>"
    clientId: "<same as oidc_client_id>"
    cookieDomain: "<env-domain>"    # was oauth2-proxy extraArgs.cookie-domain
                                    # NOTE: drop the RFC 6265 leading dot — Envoy's
                                    # SecurityPolicy schema rejects ".example.com".
                                    # Modern browsers treat "example.com" identically
                                    # for cookie scope, so behaviour is unchanged.
```

### 2. Add the `security:` global

The Traefik `cloudflare` middleware was driven by
`apps.traefik.argocd.helm.values.middlewares.cloudflare.allowedCIDRs`. Move
that list into the `global.security` block:

```yaml
global:
  security:
    cloudflareCIDRs:
      - 192.168.0.0/24      # whatever the env had under middlewares.cloudflare.allowedCIDRs
    lanCIDRs: []            # extra CIDRs for routes using `access: cloudflare-and-lan`
```

> Note: in every env today, the only entries in `cloudflare.allowedCIDRs`
> are LAN ranges (no actual Cloudflare published CIDRs). If you ever want
> a route to legitimately accept Cloudflare-tunnel traffic, populate
> `cloudflareCIDRs` with the real Cloudflare CIDRs and move the LAN ranges
> to `lanCIDRs`. Existing routes default to `cloudflare-only`, so they keep
> the current behaviour.

### 3. Swap the `traefik:` app block for `envoy-gateway:`

```yaml
apps:
  envoy-gateway:
    enabled: true
    argocd:
      targetRevision: ~
      helm:
        values:
          loadBalancerIP: <same IP previously on traefik>
          additionalExposedPorts:
            # Anything custom this env needs (e.g. plex 32400/TCP, dns: null)
```

The old `oauth2-proxy.extraArgs.*` block (issuer, cookie-domain,
whitelist-domain, redirect-url) is gone — those values are now globals.

### 4. Verify Akeyless has the OIDC client secret

The new `ExternalSecret` reads from `<global.akeyless.path>/oidc/oauth2-proxy/client_secret`
(the same path oauth2-proxy was using). Confirm it exists in the env's
Akeyless tree before deploying.

### 5. Register Cognito callback URLs

Native Envoy OIDC requires a registered callback per protected hostname.
For each oauth-protected route in `apps/*/values.yaml` (anywhere `oauth: true`
is set), register `https://<subdomain>.<env-domain>/oauth2/callback` in the
env's Cognito app client. For hess.pm the list is 35 URLs (one per
oauth-protected hostname plus argocd).

You can regenerate the list per env with:

```bash
python3 - <<'PY' <env-domain>
import re, pathlib, sys
domain = sys.argv[1]
hostnames = {f"argocd.{domain}"}
for vy in pathlib.Path("apps").glob("*/values.yaml"):
    text = vy.read_text()
    in_https = False
    cur_sub = None; cur_oauth = False; cur_indent = None
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("https:"): in_https = True; continue
        if not in_https: continue
        if line and not line[0].isspace():
            if cur_sub and cur_oauth: hostnames.add(f"{cur_sub}.{domain}")
            in_https = False; cur_sub = None; cur_oauth = False; cur_indent = None
            continue
        m = re.match(r"^(\s*)-\s*(.*)$", line)
        if m and (cur_indent is None or len(m.group(1)) <= cur_indent):
            if cur_sub and cur_oauth: hostnames.add(f"{cur_sub}.{domain}")
            cur_sub = None; cur_oauth = False; cur_indent = len(m.group(1))
            sm = re.match(r"subdomain:\s*([^\s]+)", m.group(2))
            if sm: cur_sub = sm.group(1).strip("\"'")
            continue
        sm = re.match(r"^\s+subdomain:\s*([^\s]+)", line)
        if sm: cur_sub = sm.group(1).strip("\"'")
        if re.match(r"^\s+oauth:\s*true\s*$", line): cur_oauth = True
    if cur_sub and cur_oauth: hostnames.add(f"{cur_sub}.{domain}")
for h in sorted(hostnames):
    print(f"https://{h}/oauth2/callback")
PY
```

Note that Cognito allows up to 100 callback URLs per app client by default —
plenty of headroom.

### 6. Sync and watch the rollout

ArgoCD sync waves stay the same: `envoy-gateway` is wave 1, apps come later.
Expect ingress downtime during the cutover — the old Traefik Gateway is
deleted and the new Envoy Gateway has to come up before HTTPRoutes program.

### 7. Smoke tests

After the env reconciles:

- `kubectl get gateway -n argocd envoy-gateway -o yaml` — should be `Programmed`.
- `kubectl get httproute -A` — every route should have status `Accepted`.
- `kubectl get securitypolicy -A` — every SecurityPolicy should be `Accepted`.
- Hit one oauth-protected route from a LAN client — Cognito redirect fires,
  callback succeeds, app loads.
- Hit any route from a non-allowlisted IP — should return 403
  (`RBAC: access denied`) before any backend hit.

## Pre-existing issues worth fixing during the env migration

These predate this PR but are worth cleaning up while the env is being
touched:

- `apps/n8n/values.yaml` — second ingress entry uses stale
  `priority` / `matchSuffix` fields (pre-Gateway-API). It currently renders
  as a duplicate catch-all and the path-bypass it was meant to do is broken.
  Convert to `pathPrefixes:` or `rawMatch:`.
- `apps/pytr/templates/login-ingressroute.yaml` — still uses Traefik
  `IngressRoute` CRD and references the retired
  `global.traefik.middlewareNamespace`. Replace with a Gateway-API
  `HTTPRoute` plus an Envoy `SecurityPolicy` that protects it.
- `apps/samba/values.yaml` — uses stale `traefikEntryPoint:` (renamed to
  `listener:` in the prior Gateway API migration).

## Rollback

If the migration fails in your env, the cleanest rollback is to pin
`global.argocd.targetRevision` (in your env's `global:` block) to the last
commit on `main` before the Envoy work, and let ArgoCD resync. Don't try to
partially revert — Traefik and Envoy don't coexist cleanly in this codebase
anymore.

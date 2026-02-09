# Traefik Helm Chart Upgrade: 37.x → 39.0.0

## Breaking Changes Addressed

### 1. Middlewares moved under `http` key (v39 requirement)

v39 enforces that all HTTP-related port options must be nested under an explicit `http:` key. Schema validation rejects the old structure.

```yaml
# Before (v37)
ports:
  websecure:
    middlewares:
      - "argocd-cloudflare@kubernetescrd"

# After (v39)
ports:
  websecure:
    http:
      middlewares:
        - "argocd-cloudflare@kubernetescrd"
```

### 2. TLS config moved from top-level to `ports.websecure.http.tls`

The top-level `tls` key with `enabled`/`options`/`certResolver` is not a valid schema structure in v39. TLS configuration for entrypoints now lives under `ports.<name>.http.tls`.

```yaml
# Removed
tls:
  enabled: true
  options: ""
  certResolver: "cloudflare"

# Added under ports.websecure.http
http:
  tls:
    enabled: true
    certResolver: "cloudflare"
```

Domain-specific TLS config (`main`, `sans`) remains in `additionalArguments` as before.

### 3. Removed `accessLogs` (invalid top-level key)

v38+ introduced strict schema enforcement — unknown top-level keys cause Helm validation errors. `accessLogs` was never a valid Traefik Helm chart key; access logging is configured under `logs.access`, which was already present and correct:

```yaml
# Removed (invalid, silently ignored before v38)
accessLogs:
  enabled: true

# Already present and correct — no change needed
logs:
  access:
    enabled: true
    fields:
      defaultMode: keep
```

### 4. Removed `ssl` (invalid top-level key)

Same schema enforcement issue. `ssl` was never a valid chart key; the equivalent functionality is provided by `serversTransport.insecureSkipVerify`, which was already present:

```yaml
# Removed (invalid, silently ignored before v38)
ssl:
  insecureSkipVerify: true

# Already present and correct — no change needed
serversTransport:
  insecureSkipVerify: true
```

## Pre-deployment Requirements

- **CRDs must be upgraded before the chart.** Apply the latest Traefik CRDs on the cluster before ArgoCD syncs the new version. The separate `traefik-crds` chart is deprecated since v38.0.2; apply CRDs directly from the Traefik Helm chart repo.

## Behavioral Changes to Monitor

- **Encoded path handling** (Traefik 3.6.4+ / 3.6.7+): Stricter handling of encoded characters in URLs (encoded slashes, backslashes, null characters, etc.) due to security fix GHSA-gm3x-23wp-hc2c. Services relying on unusual URL encodings should be tested. Old behavior can be restored per-entrypoint via `ports.<name>.http.encodedCharacters` but this is not recommended.
- **Traefik Hub**: v39 only supports Hub v3.19.0+. Not relevant for this deployment (Hub is not used).
- **Provider changes** (v38): `kubernetesGateway`, `knative`, and `kubernetesIngressNginx` label selector behavior changed. Not relevant — only `kubernetesCRD` provider is used.

# Generic Helm Chart

Shared base chart used as a dependency by 52 application charts in this repository. It provides standardized templates for Deployments, Services, Ingress routes, PVCs, ConfigMaps, External Secrets, and Backups.

## Making changes

**Any change to this chart (templates, schema, default values) requires a version bump.** Helm resolves dependencies by version, so dependent app charts won't pick up your changes until:

1. The generic chart version is incremented
2. Every dependent app's `Chart.yaml` is updated to reference the new version
3. `helm dependency update` is run in each dependent app directory

A script automates all three steps:

```bash
cd apps
bash ./bump_generic_chart.sh
```

Run this script **every time** you change anything under `apps/generic/`.

### Change checklist

1. Edit the template(s) in `templates/`
2. If adding new values: update `values.schema.json` with the field definition
3. Run `bash ./bump_generic_chart.sh` from the `apps/` directory
4. Commit all generated changes (Chart.yaml, Chart.lock, .tgz files across all app dirs)

### When you do NOT need to bump

- Editing only an app's own `values.yaml` (e.g., `apps/plex/values.yaml`) -- ArgoCD picks those up directly
- Changing environment-level values (e.g., `homelab-environments/unsereiner.net/values.yaml`)
- Adding a new app that already references the current generic chart version

## Structure

```
generic/
  Chart.yaml              # Chart metadata and version (bumped by script)
  values.yaml             # Default values and examples
  values.schema.json      # JSON Schema for value validation
  templates/
    deployment.yaml       # Deployment with init containers, mounts, probes, env
    service.yaml          # ClusterIP service
    ingress-https.yaml    # Traefik HTTPS IngressRoute with TLS
    ingress-tcp.yaml      # Traefik TCP IngressRoute
    ingress-udp.yaml      # Traefik UDP IngressRoute
    configmap.yaml        # ConfigMaps from values
    external-secrets.yaml # ExternalSecret resources (Akeyless integration)
    pv.yaml               # Standalone PersistentVolumes
    pvc.yaml              # Standalone PersistentVolumeClaims
    backup-crd.yaml       # Backup annotations for restic
```

## Key values

| Value | Type | Description |
|---|---|---|
| `deployment.image` | string | **Required.** Container image |
| `deployment.ports` | array | Container ports to expose |
| `deployment.hostNetwork` | bool | Use host network namespace (opt-in, default: not set) |
| `deployment.dnsPolicy` | string | Pod DNS policy (`ClusterFirst`, `ClusterFirstWithHostNet`, etc.) |
| `deployment.securityContext` | object | Container security context |
| `deployment.initContainers` | array | Init containers (passed through as-is) |
| `deployment.pvcMounts` | object | PVC mounts with optional backup config |
| `deployment.deviceMounts` | object | Host device mounts (CharDevice) |
| `deployment.configMapMounts` | object | ConfigMap volume mounts |
| `deployment.envFrom` | object | ConfigMap/Secret references for env vars |
| `deployment.resources` | object | CPU/memory requests and limits |
| `deployment.livenessProbe` | object | Liveness probe config |
| `deployment.readinessProbe` | object | Readiness probe config |
| `deployment.imagePullPolicy` | string | Pull policy (default: `IfNotPresent`) |
| `deployment.imagePullSecrets` | array | Image pull secret names |
| `service.ports` | array | Service port definitions |
| `ingress.https` | array | HTTPS ingress routes (requires `global.domain`) |
| `ingress.tcp` | array | TCP ingress routes |
| `ingress.udp` | array | UDP ingress routes |
| `configMaps` | object | ConfigMap definitions (key-value pairs) |
| `externalSecrets` | object | External Secrets (Akeyless) definitions |

See `values.schema.json` for the full schema with descriptions and validation rules.

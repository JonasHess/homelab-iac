# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Kubernetes/Helm-based Infrastructure as Code (IaC) repository for managing a comprehensive homelab environment using GitOps principles with ArgoCD.

## Key Commands

### Initial Setup
```bash
# Bootstrap ArgoCD and the base chart; kube-context defaults to global.cluster.cluster-name
./scripts/setup.sh <repoURL> <targetRevision> <path-to-env-values.yaml> [<kube-context>]
```

### Bump Generic Chart & Update Dependencies
```bash
cd apps && ./bump_generic_chart.sh  # Bumps generic chart version and propagates to all app charts
```

### Create New Application
```bash
python scripts/helm-tools/create_app.py  # Interactive tool to create new app chart
```

### Tests (also run in CI on PRs and on main)
```bash
./tests/charts/render-all.sh   # renders every app chart, checks Chart.lock vs charts/*.tgz
kubeconform -strict -summary -ignore-missing-schemas tests/charts/.rendered/*.yaml
./tests/alert-rules/render-rules.sh && (cd tests/alert-rules && promtool test rules *.test.yaml)
python -m pytest -q tests/restic tests/firefly-jobs   # needs pytest + the scripts' requirements
```
Renovate auto-merges only when every check passes, so a red check stops a bad bump from reaching ArgoCD.


## Architecture

### Helm Chart Structure
- **Generic Chart Pattern**: Most applications use `apps/generic/` as a base dependency
- **App Structure**: Each app in `apps/<appname>/` contains:
  - `Chart.yaml` - Helm chart metadata with generic chart dependency
  - `values.yaml` - Application-specific configuration
  - `templates/` - Additional Kubernetes resources (if needed)
- **Custom Template Apps**: Some apps like `restic` and `filecleanup` use custom templates for specialized functionality (e.g., CronJobs)

### Key Components
- **ArgoCD**: Manages all deployments via GitOps from `apps/argocd/`
- **Akeyless**: Secrets management, integrated via External Secrets Operator
- **Envoy Gateway**: Gateway API controller (`apps/envoy-gateway/`) handling HTTP/HTTPS/TCP/UDP routing. One `Gateway` in namespace `envoy-gateway`; app HTTPRoutes are created there too. LAN listeners on :443 (`websecure`), Cloudflare-only mTLS listeners on :4443 (`websecure-public`). Details: `docs/gateway-and-oidc.md`
- **OIDC**: A Gateway-level `SecurityPolicy` (`gateway-oidc`) puts AWS Cognito login in front of every route by default
- **cert-manager**: TLS certificate management with Let's Encrypt wildcard certificates via DNS-01 challenge
- **Base Chart**: Located in `base-chart/`, manages overall ArgoCD applications
- **Bootstrap Chart**: Initial ArgoCD setup in `bootstrap-chart/`

### Common Patterns

1. **External Secrets**: Most apps use External Secrets to fetch credentials from Akeyless
   ```yaml
   # Example: apps/*/templates/*-external-secret.yaml
   ```

2. **Ingress Configuration**: Apps expose services via Gateway API routes (Envoy Gateway as controller)
   ```yaml
   # Configured in values.yaml under generic.ingress.https/tcp/udp
   # Every https entry must set oauth (schema-required):
   #   oauth: true   -> route stays behind the Gateway-level Cognito OIDC
   #   oauth: false  -> a route-level SecurityPolicy opts it out (app's own auth only)
   #   groups: [...] -> with oauth: true, only these Cognito groups get through
   #                    (default: global.oidc.defaultGroups)
   # A second entry with pathPrefixes can carve out paths; the longer prefix wins.
   # generic.ingress.groups: app-wide groups for oauth: true routes without their own.
   # Environments restrict an app with this instead of copying its https list
   # (Helm replaces lists whole, so a copy silently pins the routes).
   ```

3. **Persistent Storage**: hostPath PersistentVolumes, one per mount
   ```yaml
   # generic.deployment.pvcMounts (mounted into the app) or generic.persistentVolumeClaims
   # (standalone claim, e.g. for a CronJob). hostPath stays empty in this repo and is
   # set per environment in homelab_environments. The PV has type Directory, so the
   # host directory must be created (mkdir) BEFORE the change is deployed.
   ```

4. **Extra pods in an app chart** (Jobs, CronJobs): never label their pods `app: <appName>`.
   The generic Service selects on exactly that label, so such a pod joins the Service
   while it runs and receives the app's traffic. Use e.g. `app: <appName>-dump`.

## Working with Applications

When modifying or adding applications:
1. Each app uses the generic chart as a dependency - check `apps/generic/values.yaml` for available options
2. Application-specific templates go in `apps/<appname>/templates/`
3. Only after changing `apps/generic/` run `cd apps && ./bump_generic_chart.sh` (see below)
4. Changes are automatically deployed by ArgoCD once pushed to the repository

## Important Configurations

- **Namespace**: Almost all apps deploy to `argocd` (the base-chart default); exceptions are set via `apps.<app>.argocd.namespace` (e.g. `app-immich`, `cert-manager`)
- **Storage**: hostPath PVs (storageClassName `manual` for pvcMounts, `''` for persistentVolumeClaims), Retain policy
- **Secrets**: Stored in Akeyless, accessed via External Secrets Operator
- **Ingress**: HTTPS endpoints via Gateway API HTTPRoutes; authentication by Envoy Gateway OIDC (`oauth: true|false` per route)
- **TLS**: Wildcard certificate managed by cert-manager (ClusterIssuer with Cloudflare DNS-01)

## Helm Dependency Notes — When to Run `bump_generic_chart.sh`

**Run it ONLY** when you changed files inside `apps/generic/` (templates, helpers, `values.schema.json`, default `values.yaml`).

**Do NOT run it** when:
- Editing an app's own `values.yaml` or `templates/` — ArgoCD deploys those changes automatically on push.
- Adding a new app that already references the current generic chart version.
- Changing anything outside `apps/generic/` (e.g., `base-chart/`, `bootstrap-chart/`, `scripts/`).

## Generic Chart Capabilities

The `apps/generic/` chart provides comprehensive Kubernetes resource templating with the following capabilities:

### Core Resources
- **Deployment**: Container orchestration with init containers, resource limits, security contexts, and volume mounts
- **Service**: Multi-port service definitions with TCP/UDP protocol support
- **Ingress**: HTTPS (Gateway API HTTPRoute), TCP (TCPRoute), and UDP (UDPRoute) ingress configurations
- **PVC/PV**: Persistent storage with automatic PV/PVC creation for hostPath volumes

### Advanced Features
- **External Secrets**: Integration with Akeyless via External Secrets Operator for secure credential management
- **Backup Integration**: Restic backup CRDs with configurable include/exclude patterns and restore capabilities
- **Volume Mounting**: Support for PVC mounts, device mounts (CharDevice), and ConfigMap mounts
- **Environment Variables**: ConfigMap and Secret references via envFrom

### Configuration Options
- **Resource Management**: CPU/memory requests and limits
- **Security**: Container security contexts and privileged access
- **Networking**: Multi-port services with named ports and protocols
- **Storage**: Manual storage class with ReadWriteMany access, backup annotations
- **Ingress**: Gateway API routing with per-route OIDC opt-out (`oauth`) and Cognito group checks (`groups`), subdomain/domain routing, pathPrefixes and rawMatch matching

### Backup System
- Automatic ResticBackup CRD generation for PVC mounts with backup.enabled=true
- Configurable backup patterns (include/exclude), size limits, cache exclusion
- Restore capability with restic/restore labels

## Base Chart Application Management

The `base-chart/values.yaml` file serves as the central registry for all applications in the homelab. It defines:

### Global Configuration
- **Domain**: `home-server.dev` - Base domain for all applications
- **External Secrets**: Integration with Akeyless secret store (`akeyless-secret-store`)
- **Authentication**: Envoy Gateway OIDC with AWS Cognito (`global.oidc`, incl. `defaultGroups`)
- **Email Settings**: Global email configuration for various services
- **Sync Policies**: Default per app: automated (prune + selfHeal) with retry (10 attempts, backoff up to 5m); global ignoreDifferences

### Application Registry
Each application is defined under `apps.<appname>` with:
- **enabled**: Boolean flag to enable/disable deployment
- **homer**: Dashboard configuration (group, logo, subtitle, displayName, subdomain)
- **argocd**: ArgoCD application settings (targetRevision, namespace, project, syncWave)

### Sync Wave Ordering
Applications are deployed in phases using `syncWave`:
- **Wave 0**: ArgoCD, Akeyless (installs External Secrets, which later waves already use)
- **Wave 1**: Envoy Gateway, cert-manager, Reloader (ingress, TLS & core services)
- **Wave 2**: Prometheus, Crossplane (monitoring & infrastructure)
- **Wave 3-5**: DNS (cloudflareddns), AWS/ECR helper, Postgres, Redis
- **Wave 10-18**: Applications
- **Wave 20+**: Media, downloaders, maintenance jobs

Waves only order creation: the parent does not wait for a child Application to be healthy. Apps that sync before a CRD/operator exists rely on the sync retry and on `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true` on custom resources.

### Homer Dashboard Groups
Centralized group definitions for dashboard organization:
- **Infrastructure**: Core services (ArgoCD, Envoy Gateway, monitoring)
- **Monitoring**: Grafana, Prometheus dashboards
- **Productivity**: Document management, passwords
- **Smart Home**: Home Assistant, Zigbee
- **Media & Entertainment**: Plex, Jellyfin, Immich
- **Starrs**: Radarr, Sonarr, Prowlarr (*arr applications)
- **Downloads**: qBittorrent, SABnzbd
- **AI & ML**: Ollama, Open WebUI

## How to Add New Applications

### 1. Create Application Directory
```bash
mkdir apps/<appname>
cd apps/<appname>
```

### 2. Create Chart.yaml
```yaml
apiVersion: v2
appVersion: 1.0.0
dependencies:
- name: generic
  repository: file://../generic
  version: X.X.X  # look up current version in apps/generic/Chart.yaml
description: Helm chart for <appname>
name: <appname>
type: application
version: 0.1.0
```

### 3. Create values.yaml
```yaml
generic:
  deployment:
    image: <container-image>
    ports:
    - containerPort: <port>
    pvcMounts:
      config:
        mountPath: /config
        hostPath: /mnt/storage/<appname>/config
  service:
    ports:
    - name: http
      port: <port>
  ingress:
    https:
    - subdomain: <appname>
      port: <port>
      oauth: true   # required; false only if the app has its own auth
```

Set the real `hostPath` values in `homelab_environments/<env>/values.yaml` and create those directories on the host before deploying.

### 4. Add to base-chart/values.yaml
```yaml
apps:
  <appname>:
    enabled: true
    homer:
      enabled: true
      group: "<group-name>"
      logo: "<icon-url>"
      subtitle: "<description>"
      displayName: "<display-name>"
    argocd:
      targetRevision: ~
      namespace: "argocd"
      project: "default"
      syncWave: "15"
```

### 5. Update Dependencies
```bash
cd apps && ./bump_generic_chart.sh
```

The application will be automatically deployed by ArgoCD once pushed to the repository.

## File Cleanup Application

The `filecleanup` app provides automated cleanup of old files and empty directories using CronJobs.

### Features
- Automated file deletion based on age (retention days)
- Optional empty directory cleanup
- Dry-run mode for testing
- Per-job configuration with individual schedules and retention policies
- Mounts existing PVCs to clean specific paths

### Configuration
```yaml
filecleanup:
  dryRun: true  # Global dry-run toggle for testing
  cleanupJobs:
    job-name:  # Also used as PVC reference
      retentionDays: 7          # Delete files/dirs older than this
      cleanupEmptyDirs: true     # Also remove empty directories
      schedule: "0 2 * * *"      # Cron schedule
```

The job name must match a PVC defined in `generic.persistentVolumeClaims`.

## External Configuration Pattern (smarthome4)

The `smarthome4` app uses a **child ArgoCD Application** to deploy its configuration separately from the main chart:

- The smarthome4 chart creates an ArgoCD Application (`smarthome4-config-application.yaml`) that points to a standalone Helm chart in the `homelab-environments` repo
- The config chart (`hess.pm/smarthome4-config/`) renders a ConfigMap with the application's YAML configuration
- Configuration values (`externalConfig.repoURL`, `targetRevision`, `path`, `configMapName`) are passed from the environment values through to the child Application
- This pattern gives the config its own ArgoCD sync lifecycle, independent of the main smarthome4 deployment

## smarthome4-ui Application

The `smarthome4-ui` app is a FastAPI + React web UI for managing Zigbee2MQTT scenes. It uses the **generic chart pattern** (like zigbee2mqtt, openwebui).

- **Image**: Private ECR (`hess.pm/smarthome4-ui`)
- **Port**: 8000
- **Ingress**: subdomain `lights`, `oauth: true` (Cognito OIDC)
- **Secrets**: Reuses smarthome4's Akeyless paths (`/smarthome4/HOMEASSISTANT_APITOKEN`, `/smarthome4/MQTT_BROKER_USERNAME`, `/smarthome4/MQTT_BROKER_PASSWORD`) with env var names the app expects (`HA_API_TOKEN`, `MQTT_USERNAME`, `MQTT_PASSWORD`)
- **ConfigMap**: Non-secret env vars (MQTT_HOST, MQTT_PORT, Z2M_TOPIC, HA_BASE_URL, CORS_ORIGINS)
- **Homer**: Smart Home group

## Claude Code Memories
- Remember to fully understand the apps/generic chart before writing code
- The generic chart uses JSON schema validation (values.schema.json) for configuration
- All resources are namespaced and use the appName as a prefix for resource naming
- New apps must be added to base-chart/values.yaml to be deployed by ArgoCD
- Use appropriate syncWave values to control deployment order
- Follow the standard Chart.yaml template with generic chart dependency
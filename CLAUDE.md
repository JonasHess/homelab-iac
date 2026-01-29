# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Kubernetes/Helm-based Infrastructure as Code (IaC) repository for managing a comprehensive homelab environment using GitOps principles with ArgoCD.

## Key Commands

### Initial Setup
```bash
./scripts/setup.sh  # Bootstrap ArgoCD and initial applications
```

### Update Helm Dependencies
```bash
cd apps && ./update_dependencies.sh  # Updates all Helm chart dependencies
```

### Create New Application
```bash
python scripts/helm-tools/create_app.py  # Interactive tool to create new app chart
```

### Deploy to Kind Cluster (for testing)
```bash
./scripts/kind-setup/deploy_kind_cluster.sh  # Creates local Kind cluster
./scripts/kind-setup/install_cloud-provider-kind.sh  # Installs cloud provider
```

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
- **Traefik**: Ingress controller handling HTTP/HTTPS routing
- **Base Chart**: Located in `base-chart/`, manages overall ArgoCD applications
- **Bootstrap Chart**: Initial ArgoCD setup in `bootstrap-chart/`

### Common Patterns

1. **External Secrets**: Most apps use External Secrets to fetch credentials from Akeyless
   ```yaml
   # Example: apps/*/templates/*-external-secret.yaml
   ```

2. **Ingress Configuration**: Apps expose services via Traefik IngressRoutes
   ```yaml
   # Configured in values.yaml under ingress.https section
   ```

3. **Persistent Storage**: Apps use PVCs with local-path storage class
   ```yaml
   # Configured in values.yaml under persistence section
   ```

## Working with Applications

When modifying or adding applications:
1. Each app uses the generic chart as a dependency - check `apps/generic/values.yaml` for available options
2. Application-specific templates go in `apps/<appname>/templates/`
3. After modifying Chart.yaml, run `cd apps && ./update_dependencies.sh`
4. Changes are automatically deployed by ArgoCD once pushed to the repository

## Important Configurations

- **Namespace**: Most apps deploy to `services` namespace
- **Storage**: Uses `local-path` storage class for persistent volumes
- **Secrets**: Stored in Akeyless, accessed via External Secrets Operator
- **Ingress**: HTTPS endpoints configured with Traefik middleware for authentication

## Helm Dependency Notes
- The `update_dependencies.sh` script only needs to be run after changes were made in the "generic" helm chart. 

## Generic Chart Capabilities

The `apps/generic/` chart provides comprehensive Kubernetes resource templating with the following capabilities:

### Core Resources
- **Deployment**: Container orchestration with init containers, resource limits, security contexts, and volume mounts
- **Service**: Multi-port service definitions with TCP/UDP protocol support
- **Ingress**: HTTPS (Traefik IngressRoute), TCP, and UDP ingress configurations
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
- **Ingress**: Cloudflare cert resolver, middleware support, subdomain/domain routing

### Backup System
- Automatic ResticBackup CRD generation for PVC mounts with backup.enabled=true
- Configurable backup patterns (include/exclude), size limits, cache exclusion
- Restore capability with restic/restore labels

## Base Chart Application Management

The `base-chart/values.yaml` file serves as the central registry for all applications in the homelab. It defines:

### Global Configuration
- **Domain**: `home-server.dev` - Base domain for all applications
- **External Secrets**: Integration with Akeyless secret store (`akeyless-secret-store`)
- **Authentication**: Traefik Forward Auth with AWS Cognito OIDC
- **Email Settings**: Global email configuration for various services
- **Sync Policies**: Global ArgoCD sync policies and ignore differences

### Application Registry
Each application is defined under `apps.<appname>` with:
- **enabled**: Boolean flag to enable/disable deployment
- **homer**: Dashboard configuration (group, logo, subtitle, displayName, subdomain)
- **argocd**: ArgoCD application settings (targetRevision, namespace, project, syncWave)

### Sync Wave Ordering
Applications are deployed in phases using `syncWave`:
- **Wave 0**: ArgoCD (core GitOps)
- **Wave 1**: Traefik, Reloader (ingress & core services)
- **Wave 2**: Prometheus, Crossplane (monitoring & infrastructure)
- **Wave 3-5**: DNS, Auth, Database services
- **Wave 10-15**: Core applications
- **Wave 15-20**: Media & productivity apps
- **Wave 20+**: Backup & monitoring tools

### Homer Dashboard Groups
Centralized group definitions for dashboard organization:
- **Infrastructure**: Core services (ArgoCD, Traefik, monitoring)
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
  version: 0.1.17
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
```

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
cd apps && ./update_dependencies.sh
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

## Claude Code Memories
- Remember to fully understand the apps/generic chart before writing code
- The generic chart uses JSON schema validation (values.schema.json) for configuration
- All resources are namespaced and use the appName as a prefix for resource naming
- New apps must be added to base-chart/values.yaml to be deployed by ArgoCD
- Use appropriate syncWave values to control deployment order
- Follow the standard Chart.yaml template with generic chart dependency
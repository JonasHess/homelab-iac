# Restic CRD-Based Backup System

This directory contains a complete rewrite of the restic backup system using Kubernetes Custom Resource Definitions (CRDs) for declarative backup configuration.

## Architecture Overview

### Components

- **ResticBackup CRD**: Defines backup configurations for PVCs
- **Python Operations Script**: Unified backup and restore logic
- **RBAC Resources**: ServiceAccount with minimal required permissions
- **CronJobs**: Scheduled backup and manual restore operations

### Data Flow

1. **Backup**: `ResticBackup` CRDs with `backup.homelab.dev/enabled=true` → Discovery → Individual backup execution
2. **Restore**: `ResticBackup` CRDs with `backup.homelab.dev/restore=true` → Discovery → Individual restore execution

## Usage

### Creating a Backup Configuration

```yaml
apiVersion: backup.homelab.dev/v1
kind: ResticBackup
metadata:
  name: my-app-backup
  labels:
    backup.homelab.dev/enabled: "true"
spec:
  pvcName: my-app-data-pvc
  namespace: default
  include:
    - "**/*.jpg"
    - "**/*.png"
  exclude:
    - "**/cache/**"
    - "**/temp/**"
  excludeLargerThan: "100M"
  excludeCaches: true
```

### Triggering Backups

Backups run automatically via CronJob (daily at 2 AM by default).

Manual backup:
```bash
kubectl create job --from=cronjob/restic-backup backup-manual-$(date +%Y%m%d)
```

### Triggering Restores

1. Label CRDs for restore:
```bash
kubectl label resticbackup my-app-backup backup.homelab.dev/restore=true
```

2. Set restore date (if different from default):
```bash
kubectl patch cronjob restic-restore -p '{"spec":{"jobTemplate":{"spec":{"template":{"spec":{"containers":[{"name":"restore","env":[{"name":"RESTORE_DATE","value":"2025-05-30"}]}]}}}}}}'
```

3. Create restore job:
```bash
kubectl create job --from=cronjob/restic-restore restore-$(date +%Y%m%d)
```

4. Access restored data:
```bash
kubectl exec -it <restore-pod> -- ls /restored-data/
```

5. Clean up labels:
```bash
kubectl label resticbackup my-app-backup backup.homelab.dev/restore-
```

## Configuration

### Global Excludes

Global exclusion patterns are defined in `values.yaml`:

```yaml
globalBackupRules:
  exclude:
    - "**/*.log"
    - "**/cache/**"
    - "**/tmp/**"
    # ... more patterns
```

These are automatically applied to all backups.

### Per-CRD Configuration

Each `ResticBackup` CRD supports:

- **include**: Include only matching patterns
- **exclude**: Exclude matching patterns (in addition to global excludes)
- **excludeLargerThan**: Skip files larger than specified size
- **excludeCaches**: Skip directories with CACHEDIR.TAG
- **excludeIfPresent**: Skip directories containing specified file

## Examples

### Photo Library Backup

```yaml
apiVersion: backup.homelab.dev/v1
kind: ResticBackup
metadata:
  name: immich-photos-backup
  labels:
    backup.homelab.dev/enabled: "true"
spec:
  pvcName: immich-library-pvc
  namespace: app-immich
  include:
    - "**/*.jpg"
    - "**/*.jpeg"
    - "**/*.png"
    - "**/*.mp4"
  exclude:
    - "**/thumbnails/**"
    - "**/preview/**"
  excludeLargerThan: "1G"
```

### Document Backup

```yaml
apiVersion: backup.homelab.dev/v1
kind: ResticBackup
metadata:
  name: paperless-docs-backup
  labels:
    backup.homelab.dev/enabled: "true"
spec:
  pvcName: paperless-data-pvc
  namespace: argocd
  include:
    - "**/documents/**"
    - "**/*.pdf"
  exclude:
    - "**/index/**"
    - "**/search/**"
  excludeIfPresent: ".nobackup"
```

### Simple Full Backup

```yaml
apiVersion: backup.homelab.dev/v1
kind: ResticBackup
metadata:
  name: mealie-simple-backup
  labels:
    backup.homelab.dev/enabled: "true"
spec:
  pvcName: mealie-data-pvc
  namespace: argocd
  excludeLargerThan: "50M"
```

## Testing

### Enable Examples

Set `examples.enabled: true` in values.yaml to deploy example CRDs for testing.

### Validation Commands

```bash
# List all backup CRDs
kubectl get resticbackups

# Check backup CRD details
kubectl describe resticbackup my-app-backup

# View backup job logs
kubectl logs job/restic-backup-<timestamp>

# View restore job logs
kubectl logs job/restic-restore-<timestamp>

# Check repository status
kubectl exec -it <backup-pod> -- restic snapshots

# List available snapshots for specific date
kubectl exec -it <backup-pod> -- restic snapshots --tag backup-20250530
```

### Troubleshooting

1. **CRD Not Found**: Ensure the CRD has the correct label (`backup.homelab.dev/enabled=true`)
2. **PVC Resolution Failed**: Check that PVC exists and is bound to a PV with hostPath
3. **Permission Denied**: Verify ServiceAccount has proper RBAC permissions
4. **Path Not Found**: Ensure the resolved hostPath exists on the node

## Migration from Legacy System

The legacy ConfigMap-based system is deprecated. To migrate:

1. Create `ResticBackup` CRDs for each application
2. Remove legacy backup configurations from base-chart values
3. Test new CRDs with manual backup jobs
4. Switch to new CronJobs when validated

## Repository Maintenance

The system automatically performs:

- **Integrity checks**: 5% data verification after each backup
- **Retention policy**: Keep 30 daily, 7 weekly, 12 monthly snapshots
- **Pruning**: Remove unreferenced data to optimize storage
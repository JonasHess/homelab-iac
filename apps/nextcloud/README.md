# Nextcloud

Helm chart for Nextcloud with Collabora (Nextcloud Office).

## Prerequisites

### 1. Create Host Directories

```bash
sudo mkdir -p /mnt/tank1/encrypted/apps/nextcloud/data
sudo mkdir -p /mnt/tank1/encrypted/apps/nextcloud/postgresql
sudo chown -R 1001:1001 /mnt/tank1/encrypted/apps/nextcloud/postgresql
sudo chown -R 33:33 /mnt/tank1/encrypted/apps/nextcloud/data
```

- UID `1001` = Bitnami PostgreSQL user
- UID `33` = www-data (Nextcloud/Apache user)

### 2. Create Akeyless Secrets

Add these secrets to your Akeyless path (e.g., `/zimmermann.lat/nextcloud/`):

| Secret | Description |
|--------|-------------|
| `admin-user` | Nextcloud admin username |
| `admin-password` | Nextcloud admin password |
| `postgres-user` | PostgreSQL username (e.g., `nextcloud`) |
| `postgres-password` | PostgreSQL password |

### 3. DNS Records

Ensure these DNS records point to your server:
- `nextcloud.<your-domain>`
- `office.<your-domain>` (for Collabora)

## Features

- **Nextcloud Office (Collabora)**: Auto-installed and configured via post-installation hook
- **PostgreSQL**: Database backend via Bitnami subchart
- **Redis**: Session caching via Bitnami subchart
- **External Secrets**: Credentials managed via Akeyless

## Environment Configuration

All domain-specific values are **automatically computed** from `global.domain`. You only need to override storage paths:

```yaml
nextcloud:
  enabled: true
  argocd:
    targetRevision: ~
    helm:
      values:
        generic:
          persistentVolumeClaims:
            data:
              hostPath: /mnt/tank1/encrypted/apps/nextcloud/data
            postgresql:
              hostPath: /mnt/tank1/encrypted/apps/nextcloud/postgresql
        # All domain-specific values computed from global.domain - no overrides needed
```

**Auto-computed values** (from `global.domain` via `templates/collabora-configmap.yaml`):
- `NEXTCLOUD_TRUSTED_DOMAINS` - nextcloud.domain, office.domain, nextcloud
- `COLLABORA_URL` - internal Collabora service URL
- `COLLABORA_PUBLIC_URL` - external browser URL
- `NEXTCLOUD_CALLBACK_URL` - internal callback URL
- Collabora `server_name`, `aliasgroup1`, `extra_params`

**Notes**:
- Subdomains default to `nextcloud` and `office` - override via `collaboraConfig.nextcloudSubdomain` / `collaboraConfig.collaboraSubdomain`
- `OVERWRITEPROTOCOL` is intentionally not set - relies on `X-Forwarded-Proto` from Traefik
- The Collabora `securityContext` is required for Kubernetes compatibility

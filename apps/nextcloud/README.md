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

Override these values in your environment's `values.yaml`:

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
        nextcloud:
          nextcloud:
            host: nextcloud.<your-domain>
            trustedDomains:
              - nextcloud.<your-domain>
              - office.<your-domain>
            # Environment-specific extraEnv (replaces base values, must include all)
            extraEnv:
              - name: TRUSTED_PROXIES
                value: "10.0.0.0/8"
              - name: OVERWRITEPROTOCOL
                value: "https"
              - name: NC_loglevel
                value: "2"
              # Internal K8s service URL (Nextcloud -> Collabora)
              - name: COLLABORA_URL
                value: "http://nextcloud-collabora:9980"
              # External URL (browser -> Collabora)
              - name: COLLABORA_PUBLIC_URL
                value: "https://office.<your-domain>"
          collabora:
            collabora:
              domain: nextcloud\\.<your-domain>
              server_name: office.<your-domain>
```

**Note**: The `extraEnv` array must be specified completely because Helm arrays replace rather than merge.

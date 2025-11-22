# AWS ECR Registry Helper

This Helm chart manages authentication for multiple AWS ECR (Elastic Container Registry) accounts in Kubernetes.

## Overview

ECR authentication tokens expire after 12 hours. This chart automatically refreshes Docker registry credentials for multiple ECR accounts, allowing your cluster to pull images from private ECR repositories.

## Features

- ✅ Support for multiple AWS accounts and regions
- ✅ Automatic token refresh every 10 hours (configurable)
- ✅ Immediate credential creation on deployment
- ✅ Shared RBAC resources across all registries
- ✅ Per-registry AWS credential isolation

## Architecture

### Shared Resources
- **ServiceAccount**: `sa-argocd` - Used by all jobs
- **Role/RoleBinding**: Manages permissions for all registry secrets

### Per-Registry Resources
For each ECR registry defined in `values.yaml`:
- **ConfigMap**: Stores region, secret name, and target namespace
- **CronJob**: Refreshes ECR token on schedule
- **Job**: Creates initial credential immediately

## Configuration

### Adding a New ECR Registry

1. **Store AWS Credentials in Akeyless**

   Add the AWS credentials to Akeyless at the following paths:
   - `<akeyless-path>/aws/ecr/<registry-name>/AWS_ACCOUNT`
   - `<akeyless-path>/aws/ecr/<registry-name>/AWS_ACCESS_KEY_ID`
   - `<akeyless-path>/aws/ecr/<registry-name>/AWS_SECRET_ACCESS_KEY`

   For example, for a registry named `secondary`:
   - `<akeyless-path>/aws/ecr/secondary/AWS_ACCOUNT` = `987654321098`
   - `<akeyless-path>/aws/ecr/secondary/AWS_ACCESS_KEY_ID` = `AKIA...`
   - `<akeyless-path>/aws/ecr/secondary/AWS_SECRET_ACCESS_KEY` = `...`

   The External Secrets Operator will automatically create the Kubernetes secret.

2. **Update values.yaml**

   Add the new registry to the `ecrRegistries` list:
   ```yaml
   ecrRegistries:
     - name: primary
       region: eu-central-1
       namespace: argocd
       schedule: "0 */10 * * *"

     - name: secondary
       region: eu-central-1
       namespace: argocd
       schedule: "0 */10 * * *"
   ```

   Secret names are auto-generated from the registry name:
   - Docker secret: `regcred-{name}` (e.g., `regcred-primary`, `regcred-secondary`)
   - AWS credentials: `ecr-registry-helper-secrets-{name}`

3. **Deploy/Update the Chart**

   The chart will automatically:
   - Create External Secrets to fetch credentials from Akeyless
   - Generate ConfigMaps for each registry
   - Deploy CronJobs and immediate Jobs for token refresh

### Configuration Options

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Unique identifier for this registry (also used for naming) | `primary`, `secondary` |
| `region` | AWS region for the ECR | `eu-central-1`, `us-east-1` |
| `namespace` | Target namespace for the Docker secret | `argocd`, `default` |
| `schedule` | Cron schedule for token refresh | `"0 */10 * * *"` (every 10 hours) |

**Auto-generated names** (based on `name` field):
- Docker secret: `regcred-{name}`
- AWS credentials secret: `ecr-registry-helper-secrets-{name}`
- ConfigMap: `ecr-registry-helper-cm-{name}`
- CronJob: `ecr-registry-helper-{name}`

### Global Settings

You can customize the following global settings in `values.yaml`:

```yaml
resources:
  requests:
    memory: "64Mi"
    cpu: "100m"
  limits:
    memory: "128Mi"
    cpu: "200m"

cronJob:
  successfulJobsHistoryLimit: 2  # Keep last 2 successful job runs
  suspend: false                   # Set to true to pause all CronJobs
  backoffLimit: 3                  # Retry failed jobs up to 3 times
```

### AWS Credentials in Akeyless

For each registry, store the following in Akeyless:

**Path Pattern**: `<akeyless-path>/aws/ecr/<registry-name>/<key>`

**Required Keys**:
- `AWS_ACCOUNT`: AWS account ID (e.g., `123456789012`)
- `AWS_ACCESS_KEY_ID`: IAM access key
- `AWS_SECRET_ACCESS_KEY`: IAM secret key

**Required IAM Permissions**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    }
  ]
}
```

The External Secrets Operator automatically syncs these values into Kubernetes secrets every hour.

## Using the ECR Credentials

Once deployed, pods can pull images from ECR using the generated secrets:

### Option 1: ImagePullSecrets (Recommended)
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: my-app
spec:
  imagePullSecrets:
  - name: regcred-primary       # Primary account
  - name: regcred-secondary     # Secondary account
  containers:
  - name: app
    image: 123456789012.dkr.ecr.eu-central-1.amazonaws.com/my-app:latest
```

### Option 2: ServiceAccount Default
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-service-account
imagePullSecrets:
- name: regcred-primary
- name: regcred-secondary
```

## Monitoring

### Check CronJob Status
```bash
kubectl get cronjobs -n argocd | grep ecr-registry-helper
```

### View Job Logs
```bash
# Primary registry
kubectl logs -n argocd -l job-name=ecr-registry-helper-immediate-primary

# Secondary registry
kubectl logs -n argocd -l job-name=ecr-registry-helper-immediate-secondary
```

### Verify Secrets Exist
```bash
kubectl get secrets -n argocd | grep regcred-
```

## Troubleshooting

### Secret Not Created
1. Check job logs for errors:
   ```bash
   kubectl logs -n argocd -l job-name=ecr-registry-helper-immediate-primary
   ```
2. Verify AWS credentials are correct in Akeyless
3. Ensure IAM user has `ecr:GetAuthorizationToken` permission
4. Check if job failed and is being retried (backoffLimit: 3):
   ```bash
   kubectl get jobs -n argocd | grep ecr-registry-helper
   ```

### Image Pull Errors
1. Verify the secret exists in the correct namespace
2. Check that the secret is referenced in `imagePullSecrets`
3. Confirm the ECR image URL matches the AWS account ID

### Token Expiration
Tokens expire after 12 hours. If the CronJob fails to run:
- Check CronJob is not suspended: `kubectl get cronjob -n argocd`
- Manually trigger a job: `kubectl create job -n argocd --from=cronjob/ecr-registry-helper-primary manual-refresh`

## Example: Adding Third ECR Registry

1. **Add to values.yaml**:
```yaml
ecrRegistries:
  - name: production
    region: us-east-1
    namespace: production
    schedule: "0 */8 * * *"  # Every 8 hours
```

2. **Store credentials in Akeyless**:
   - `<akeyless-path>/aws/ecr/production/AWS_ACCOUNT` = `111122223333`
   - `<akeyless-path>/aws/ecr/production/AWS_ACCESS_KEY_ID` = `AKIA...`
   - `<akeyless-path>/aws/ecr/production/AWS_SECRET_ACCESS_KEY` = `...`

The External Secret will automatically create `ecr-registry-helper-secrets-production` and the CronJob will create `regcred-production`.

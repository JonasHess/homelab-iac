# pytr App Design Spec

## Overview

Add a `pytr` app to the homelab that downloads Trade Republic documents and feeds them into paperless-ngx for automatic processing. The app consists of two CronJobs (login + download), a temporary HTTP endpoint for OTP code submission, and shared persistent storage for session credentials.

## Components

### 1. Login CronJob (suspended, manual trigger via ArgoCD)

- **Image**: `python:3-slim`, installs `pytr==X.Y.Z` (pinned) at container startup
- **Purpose**: Authenticate with Trade Republic via web login
- **Trigger**: Manually via ArgoCD's "Create Job" button
- **Schedule**: Dummy (`0 0 1 1 *`), always `suspend: true`

Runs a Python wrapper script (stored in a ConfigMap) that:
1. Reads `PHONE_NUMBER` and `PIN` from env vars (sourced from Akeyless secret)
2. Starts `pytr login --phone_no $PHONE --pin $PIN` as a subprocess with `stdin=PIPE`
3. Starts a Python `http.server` on port 8080
4. Waits for a `GET /code/<4-digit-code>` HTTP request
5. Writes the code to the subprocess stdin
6. Returns success/failure as an HTTP response to the browser
7. Exits after login completes

Mounts:
- Credentials PVC at `/home/pytr/.pytr/` (session cookies, URL history)

### 2. Download CronJob (scheduled monthly)

- **Image**: `python:3-slim`, installs `pytr==X.Y.Z` (pinned) at container startup
- **Purpose**: Download new Trade Republic documents
- **Schedule**: `0 2 1 * *` (1st of month, 2 AM)
- **Command**: `pytr dl_docs --output /downloads`

pytr has built-in deduplication:
- Skips files that already exist locally (by filepath)
- Maintains a URL history file in `~/.pytr/` across sessions
- Safe to run repeatedly without creating duplicates

Mounts:
- Credentials PVC at `/home/pytr/.pytr/` (shared with login job)
- Downloads PVC at `/downloads` (hostPath to paperless-ngx consume folder)

Fails naturally if session is expired, signaling a re-login is needed.

### 3. Service

- Targets pods with label `component: login`
- Port 8080
- Only has endpoints when a login Job pod is running

### 4. IngressRoute

- Subdomain: `tr` (e.g. `https://tr.zimmermann.lat/code/1234`)
- Behind oauth2-proxy middleware (Cognito auth)
- Routes to the login Service on port 8080
- Returns 503 when no login Job is running (expected behavior)

### 5. External Secret

- Secret store: `akeyless-secret-store`
- Akeyless paths:
  - `/pytr/PHONE_NUMBER`
  - `/pytr/PIN`
- Mounted as env vars in the login Job

### 6. PVCs

| Name | Purpose | Shared between | hostPath (set in env repo) |
|------|---------|----------------|---------------------------|
| `pytr-credentials` | `~/.pytr/` — session cookies, URL history, credentials | Both CronJobs | Small volume, no hostPath needed (or a dedicated path) |
| `pytr-downloads` | Downloaded documents | Download CronJob only | e.g. `/mnt/tank1/encrypted/apps/paperlessngx/consume/paperless-gpt-auto/Michael/TradeRepublic` |

## Chart Structure

```
apps/pytr/
  Chart.yaml                         # generic chart dependency
  values.yaml                        # image, schedule, secrets config
  templates/
    login-cronjob.yaml               # suspended CronJob for login
    download-cronjob.yaml            # scheduled CronJob for dl_docs
    login-service.yaml               # Service targeting login pods
    login-ingressroute.yaml          # Traefik IngressRoute for /code/XXXX
    external-secret.yaml             # Akeyless secret for phone+PIN
    configmap-login-script.yaml      # Python wrapper script
```

## Login Flow

1. Trigger login Job via ArgoCD "Create Job" button on the `pytr-login` CronJob
2. Job starts, installs pytr, runs the Python wrapper
3. Wrapper initiates `pytr login` — Trade Republic sends 4-digit code to your phone
4. Open `https://tr.zimmermann.lat/code/1234` in your browser
5. Wrapper feeds code to pytr subprocess, returns success/failure in browser
6. Session credentials stored on credentials PVC
7. Job exits successfully

## base-chart and Environment Config

### base-chart/values.yaml (this repo)

```yaml
apps:
  pytr:
    enabled: false
    homer:
      enabled: false
    argocd:
      targetRevision: ~
      namespace: argocd
      project: default
      syncWave: '20'
```

### homelab_environments/zimmermann.lat/values.yaml (env repo)

```yaml
apps:
  pytr:
    enabled: true
    argocd:
      targetRevision: ~
      helm:
        values:
          generic:
            persistentVolumeClaims:
              credentials:
                hostPath: /mnt/tank1/encrypted/apps/pytr/credentials
              downloads:
                hostPath: /mnt/tank1/encrypted/apps/paperlessngx/consume/paperless-gpt-auto/Michael/TradeRepublic
          pytr:
            pytrVersion: "X.Y.Z"
            login:
              schedule: "0 0 1 1 *"
              suspend: true
            download:
              schedule: "0 2 1 * *"
              suspend: false
            externalSecret:
              enabled: true
              secretStoreName: akeyless-secret-store
            secrets:
              PHONE_NUMBER: /pytr/PHONE_NUMBER
              PIN: /pytr/PIN
```

## Technical Notes

- **Session lifetime**: pytr's session token expires every ~5 minutes but auto-refreshes via a refresh token stored in cookies. Running dl_docs monthly should keep the session alive. If the refresh token expires, re-login via ArgoCD.
- **Deduplication**: pytr skips already-downloaded files by filepath and URL history. Safe for repeated runs.
- **PVC concurrency**: Both jobs share the credentials PVC. With single-node setup and `local-path` (ReadWriteOnce), this works as both pods schedule on the same node.
- **Image startup**: `pip install pytr` adds ~10-30s to container startup. Acceptable for infrequent Job runs.

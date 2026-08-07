# justtrade

Downloads justTRADE brokerage Postbox documents into paperless-ngx, using the
`portal-document-downloader` image (the `justtrade` portal). Modelled on the
`pytr` app: a **login** CronJob handles interactive 2FA via an HTTP `/code`
endpoint, a **download** CronJob runs on a schedule and resumes the session
without 2FA.

## How it works

```
Login Job (suspended, triggered manually in ArgoCD)     Download Job (monthly)
  login.py: POST /login  → justTRADE sends a code          download.py --portal justtrade
  (SMS when mfaMethod=sms, else e-mail)                     _resume_session() → POST /refresh
  serves GET /code/<digits> at https://jt.<domain>         → fresh idToken, NO 2FA
  on code → /mfa → writes refreshToken to the PVC ───────┐  downloads all docs → /app/downloads
                                                          └─ reads the same credentials PVC
```

The refresh token (Cognito, ~30 days) lives on the shared `justtrade-credentials`
PVC as `JUSTTRADE_SESSION_FILE`, so scheduled downloads need a human only every
few weeks. If the token expires the download Job fails cleanly (it never sends an
SMS — see the download-cronjob comment) — re-trigger the login Job.

## Prerequisite: image tag

The `justtrade` portal was added **after `v11.2.0`**. Set `justtrade.imageTag`
(values.yaml) to a `portal-document-downloader` build that includes it before
enabling this app.

## Environment-repo configuration (`homelab_environments/<env>`)

This chart only defines the app logic; PVCs, the secret, and enablement come from
the env repo via the `generic` sub-chart:

```yaml
apps:
  justtrade:
    enabled: true
    argocd:
      helm:
        values:
          generic:
            persistentVolumeClaims:
              credentials:        # -> justtrade-credentials-pvc (holds the refresh token)
                hostPath: /mnt/tank1/encrypted/apps/justtrade/credentials
              downloads:          # -> justtrade-downloads-pvc (paperless consume folder)
                hostPath: /mnt/tank1/encrypted/apps/paperlessngx/consume/paperless-gpt-auto/Michael/justTRADE
            externalSecrets:
              justtrade-credentials:
                # Reuses the shared portal-document-downloader Akeyless namespace.
                - JUSTTRADE_USERNAME: /portal-document-downloader/JUSTTRADE_USERNAME
                - JUSTTRADE_PASSWORD: /portal-document-downloader/JUSTTRADE_PASSWORD
          justtrade:
            download:
              suspend: true       # un-suspend after the first login seeds the session
```

The credentials live in the same Akeyless namespace as the other portals:
add `/portal-document-downloader/JUSTTRADE_USERNAME` (Kundennummer) and
`/portal-document-downloader/JUSTTRADE_PASSWORD` (`global.akeyless.path` is
prefixed automatically).

The ingress (`jt` subdomain, behind oauth2-proxy) is already declared in this
chart's `values.yaml`.

## Logging in

1. In ArgoCD, "Create Job" from the `justtrade-login` CronJob.
2. justTRADE sends a code (SMS if `justtrade.mfaMethod=sms`, else e-mail).
3. Open `https://jt.<domain>/code/<code>` in a browser (oauth-gated).
4. The Job writes the session and exits; the download Job then runs unattended.

# InfCloud

Browser-based CalDAV/CardDAV client (JS + jQuery), fronting the [Radicale](../radicale) server. Static assets baked into a thin nginx image; runtime config injected via ConfigMap.

**Upstream is dead.** The JS app from inf-it.com has not been touched since ~2017, and `Unrud/RadicaleInfCloud` (which we use for the assets) has been stagnant since 2022-04. We accept that — InfCloud only speaks DAV, so as long as Radicale's protocol layer is stable, the UI keeps working. If it breaks, delete this app; Radicale data is unaffected.

## Why a custom image

No actively maintained InfCloud container image exists. Building locally pins the exact upstream commit (`Unrud/RadicaleInfCloud@53d3a95`) and keeps us reproducible.

## Where things live

| Concern | File |
|---|---|
| Chart, ingress, mounts, default `config.js` | `apps/infcloud/values.yaml` |
| Asset source, pinned commit | `apps/infcloud/Dockerfile` |
| HTTP routing, `/config.js` override, DAV proxy | `apps/infcloud/nginx.conf` |
| Image tag (per env) | `homelab-environments/<env>/values.yaml` |

Pod mounts: `/etc/infcloud/config.js` (ConfigMap, served by nginx at `/config.js`).

## Build & push

```bash
cd apps/infcloud
IMAGE=629099703604.dkr.ecr.eu-central-1.amazonaws.com/hess.pm/infcloud:0.13.1-1
docker build -t "$IMAGE" .
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin 629099703604.dkr.ecr.eu-central-1.amazonaws.com
docker push "$IMAGE"
```

Bump the tag in `values.yaml` after pushing. To pick up a new upstream commit, edit `INFCLOUD_COMMIT` in the `Dockerfile` and rebuild.

## Same-origin DAV proxy (no CORS)

The browser only ever talks to `contacts.<domain>`. InfCloud's `config.js` sets `href` to the **relative** path `/radicale/`, and the in-pod nginx proxies that path to the in-cluster `radicale-service:5232`. Same origin → no preflight, no `Access-Control-Allow-*` needed on Radicale.

```
browser → https://contacts.<domain>/radicale/...   (nginx in this pod)
              ↓ proxy_pass
              http://radicale-service:5232/...
```

Native DAV clients (DAVx⁵, iOS, Thunderbird) continue to hit `dav.<domain>` directly — this proxy exists **only** for the browser-loaded InfCloud JS.

If you change `config.js` `href` to an absolute `https://dav.<domain>/`, CORS preflights will start failing — that path needs Envoy-level CORS injection, which this scaffold deliberately avoids.

## `config.js` options worth knowing

Edit `generic.configMaps.infcloud-config['config.js']` in `values.yaml`:

| Key | What it does |
|---|---|
| `globalNetworkCheckSettings.href` | Base DAV URL. Must end with `/`. |
| `globalAccountSettings[].href` | Same URL; InfCloud allows multiple accounts. |
| `globalInterfaceLanguage` | `de_DE`, `en_US`, etc. — see `locales/` in the image. |
| `globalTimeZone` | IANA TZ like `Europe/Berlin`. |
| `globalDatepickerFirstDayOfWeek` | `0` Sunday, `1` Monday. |
| `globalEnableRefresh` | Manual refresh button. |
| `globalEnableJqueryAuthentication` | Keep `true` — uses XHR Basic Auth, which is what Radicale expects. |

Full reference: `config-help.txt` inside the InfCloud upstream (visible at `https://contacts.<domain>/config-help.txt` after deploy).

## Known InfCloud bugs

- **Empty user → broken Refresh button.** From the upstream README: "At least one calendar (appointments + tasks) and one addressbook must exist." Run `apps/radicale/setup-collections.sh` first.
- **Multiple files at once on upload** → InfCloud assigns random UUID slugs and ignores the HREF field. Upload one `.vcf` / `.ics` at a time.
- **Group handling differs from DAVx⁵.** InfCloud uses `CATEGORIES` (per-contact). If your DAVx⁵ install picked "separate vCards for groups," contacts created in InfCloud won't be grouped on the phone until you reconcile.

## Operations

- **Change config**: edit `values.yaml` → ArgoCD syncs → reloader rolls the pod.
- **Roll the upstream commit**: edit `Dockerfile` `INFCLOUD_COMMIT`, rebuild, push, bump tag in `values.yaml`.
- **Disable**: set `apps.infcloud.enabled: false` in `base-chart/values.yaml`. Radicale and your DAV data are untouched.
- **Debug auth flow**: open browser devtools → network → look at the `PROPFIND /radicale/...` request. Status `401` = wrong Radicale credentials. Status `502/504` = nginx can't reach `radicale-service:5232` (check the service exists and the pod is healthy). Status `2xx` = you're through, collection list works.

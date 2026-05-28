# InfCloud

Browser-based CalDAV/CardDAV client (JS + jQuery), fronting the [Radicale](../radicale) server. Pure declarative deployment — stock `nginx:alpine` serves InfCloud static assets that an init container fetches from upstream at pod start.

**Upstream is dead.** The JS app from inf-it.com has not been touched since ~2017, and `Unrud/RadicaleInfCloud` (which we use for the assets) has been stagnant since 2022-04. We accept that — InfCloud only speaks DAV, so as long as Radicale's protocol layer is stable, the UI keeps working. If it breaks, set `apps.infcloud.enabled: false`; Radicale data is unaffected.

## How it works

```
pod start
 ├─ init: alpine + busybox
 │    wget tarball from github.com/Unrud/RadicaleInfCloud@<COMMIT>
 │    extract radicale_infcloud/web/ → emptyDir
 └─ main: nginx:1.27-alpine
      serves emptyDir at /usr/share/nginx/html
      serves /config.js from ConfigMap (overrides the file in the tarball)
      proxies /radicale/ → in-cluster radicale-service:5232
```

No custom image, no ECR, no Dockerfile. The pinned upstream commit and all server config live in `values.yaml`.

## Where things live

| Concern | File |
|---|---|
| Image, init container, mounts, ingress, nginx server block, `config.js` | `apps/infcloud/values.yaml` |
| Activation per environment | `homelab-environments/<env>/values.yaml` |

Pinned InfCloud commit: `Unrud/RadicaleInfCloud@53d3a95af5b58cfa3242cef645f8d40c731a7d95` (master, 2022-04-18). Change `COMMIT=` in the init container's `args` to roll forward.

## Same-origin DAV proxy (no CORS)

The browser only talks to `contacts.<domain>`. InfCloud's `config.js` sets `href` to the **relative** path `/radicale/`. The pod's nginx proxies that path to the in-cluster `radicale-service:5232`. Same origin → no preflight, no `Access-Control-Allow-*` needed on Radicale.

```
browser → https://contacts.<domain>/radicale/...   (nginx in this pod)
              ↓ proxy_pass
              http://radicale-service:5232/...
```

Native DAV clients (DAVx⁵, iOS, Thunderbird) continue to hit `dav.<domain>` directly — this proxy exists **only** for the browser-loaded InfCloud JS. Do not change `href` to an absolute `https://dav.<domain>/` unless you also wire up Envoy-level CORS.

## `config.js` options worth knowing

Edit `generic.configMaps.infcloud-config['config.js']` in `values.yaml`:

| Key | What it does |
|---|---|
| `globalNetworkCheckSettings.href` | Base DAV URL. Keep relative (`/radicale/`). |
| `globalAccountSettings[].href` | Same URL; InfCloud allows multiple accounts. |
| `globalInterfaceLanguage` | `de_DE`, `en_US`, etc. — see `locales/` inside the upstream tarball. |
| `globalTimeZone` | IANA TZ like `Europe/Berlin`. |
| `globalDatepickerFirstDayOfWeek` | `0` Sunday, `1` Monday. |
| `globalEnableRefresh` | Manual refresh button. |
| `globalEnableJqueryAuthentication` | Keep `true` — XHR Basic Auth, what Radicale expects. |

Full reference: visible at `https://contacts.<domain>/config-help.txt` after deploy (the file is part of the upstream tarball).

## Known InfCloud bugs

- **Empty user → broken Refresh button.** From the upstream README: "At least one calendar (appointments + tasks) and one addressbook must exist." Run `apps/radicale/setup-collections.sh` first.
- **Multiple files at once on upload** → InfCloud assigns random UUID slugs and ignores the HREF field. Upload one `.vcf` / `.ics` at a time.
- **Group handling differs from DAVx⁵.** InfCloud uses `CATEGORIES` (per-contact). If your DAVx⁵ install picked "separate vCards for groups," contacts created in InfCloud won't be grouped on the phone until you reconcile.

## Operations

- **Change config**: edit `values.yaml` → ArgoCD syncs → reloader rolls the pod.
- **Roll the upstream commit**: change `COMMIT=` in the init container `args` in `values.yaml`. ArgoCD syncs → reloader rolls the pod → init container re-fetches the new assets.
- **Disable**: set `apps.infcloud.enabled: false` in `base-chart/values.yaml`. Radicale and your DAV data are untouched.
- **Pod stuck in `Init`**: kubelet can't reach `github.com` from inside the cluster. Check egress / DNS. The init container retries with the pod's normal restart policy.
- **Debug auth flow**: open browser devtools → network → look at the `PROPFIND /radicale/...` request. Status `401` = wrong Radicale credentials. Status `502/504` = nginx can't reach `radicale-service:5232` (check the service exists and the pod is healthy). Status `2xx` = you're through.

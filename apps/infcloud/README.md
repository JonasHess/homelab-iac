# InfCloud

Browser-based CalDAV/CardDAV client (JS + jQuery), fronting the [Radicale](../radicale) server. Pure declarative deployment — stock `nginx:alpine` serves InfCloud static assets that an init container fetches from upstream at pod start.

**Upstream is dead.** The JS app from inf-it.com has not been touched since ~2017, and `Unrud/RadicaleInfCloud` (which we use for the assets) has been stagnant since 2022-04. We accept that — InfCloud only speaks DAV, so as long as Radicale's protocol layer is stable, the UI keeps working. If it breaks, set `apps.infcloud.enabled: false`; Radicale data is unaffected.

## How it works

```
pod start
 ├─ init: alpine + busybox
 │    wget tarball from github.com/Unrud/RadicaleInfCloud@<COMMIT>
 │    extract radicale_infcloud/web/ → emptyDir
 │    sed-inject <script src="config-overrides.js"> into index.html
 │    (so our overrides load right after upstream config.js)
 └─ main: nginx:1.27-alpine
      serves emptyDir at /usr/share/nginx/html
      serves /config-overrides.js from ConfigMap
      proxies /radicale/ → in-cluster radicale-service:5232
```

No custom image, no ECR, no Dockerfile. The pinned upstream commit and the small list of overrides live in `values.yaml`.

We deliberately **do not shadow upstream `config.js`** — it defines ~50 globals (sort alphabets, datepicker formats, search transforms, business hours, etc.) that other InfCloud JS files reference unconditionally. Owning the full file means whack-a-mole every time a global is missed. Instead, our `config-overrides.js` runs after upstream defaults and mutates the handful of values we care about.

## Where things live

| Concern | File |
|---|---|
| Image, init container, mounts, ingress, nginx server block, `config.js` | `apps/infcloud/values.yaml` |
| Activation per environment | `homelab-environments/<env>/values.yaml` |

Pinned InfCloud commit: `Unrud/RadicaleInfCloud@53d3a95af5b58cfa3242cef645f8d40c731a7d95` (master, 2022-04-18). Change `COMMIT=` in the init container's `args` to roll forward.

## Same-origin DAV proxy (no CORS)

The browser only talks to `contacts.<domain>`. InfCloud's `config.js` sets `href` to `https://contacts.<domain>/radicale/` — same origin as InfCloud itself, so no CORS preflight. The pod's nginx proxies the `/radicale/` path to the in-cluster `radicale-service:5232`.

```
browser → https://contacts.<domain>/radicale/...   (nginx in this pod)
              ↓ proxy_pass
              http://radicale-service:5232/...
```

The URL **must be absolute** even though it points back at the same host. InfCloud's `data_process.js` parses the account URL with `^https?://…` — a relative `/radicale/` makes the regex return null and the JS dies silently after login (symptom: blank page). The `global.domain` value is interpolated by Helm's `tpl` in the generic chart's `configmap.yaml`, so the URL stays env-correct.

Native DAV clients (DAVx⁵, iOS, Thunderbird) continue to hit `dav.<domain>` directly — this proxy exists **only** for the browser-loaded InfCloud JS. Do not change `href` to `https://dav.<domain>/`: that would be cross-origin and require Envoy-level CORS.

## Overrides worth knowing

Edit `generic.configMaps.infcloud-config['config-overrides.js']` in `values.yaml`. The file is loaded after upstream `config.js`, so use **assignments** (not `var` declarations) to mutate the existing globals:

```js
globalNetworkCheckSettings.href = "https://contacts.<domain>/radicale/";  // required, absolute
globalInterfaceLanguage = "de_DE";                                         // upstream default: en_US
// Example:
// globalTimeZone = "America/New_York";          // upstream default: Europe/Berlin
// globalDatepickerFirstDayOfWeek = 0;           // upstream default: 1 (Monday)
```

**Do not** add `var globalAccountSettings = [...]`. The `globalNetworkCheckSettings` flow already constructs that array dynamically with the right `userAuth`; declaring it statically makes runCardDAV crash on `globalAccountSettings[0].userAuth.userName`.

Full upstream reference: `https://contacts.<domain>/config-help.txt` after deploy.

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

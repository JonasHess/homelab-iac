# Radicale

CalDAV/CardDAV server. Auth via htpasswd (bcrypt). **No oauth2-proxy middleware** — DAV clients use HTTP Basic Auth and break on OAuth redirects.

## Prerequisites

**1. Host directory.** UID 2999 = the `radicale` user in `tomsquest/docker-radicale`:

```bash
sudo mkdir -p /mnt/tank1/encrypted/apps/radicale/data
sudo chown -R 2999:2999 /mnt/tank1/encrypted/apps/radicale/data
```

**2. htpasswd in Akeyless.** Generate bcrypt hashes, concatenate, store the blob at `<global.akeyless.path>/radicale/HTPASSWD`:

```bash
sudo apt install apache2-utils
htpasswd -nB user1
htpasswd -nB user2
# blob:
# user1:$2y$05$...
# user2:$2y$05$...
```

**3. DNS.** Point `dav.<your-domain>` at your cluster ingress.

## Where things live

| Concern | File |
|---|---|
| Chart, image, ingress, mounts, Radicale config file | `apps/radicale/values.yaml` |
| Rights file, data PVC `hostPath` | `homelab_environments/<env>/values.yaml` |
| Password hashes | Akeyless `/radicale/HTPASSWD` |

Pod mounts: `/data` (PVC), `/config` (ConfigMap: Radicale config + rights), `/etc/radicale-htpasswd` (Secret: htpasswd file).

## Rights file

Radicale evaluates sections top-to-bottom; any match grants access.

```ini
[section-label]
user = <regex>
collection = <regex>
permissions = <letters>
```

Permission letters (`RrWw` = full r/w):

| Letter | Meaning |
|---|---|
| `R` | Read the collection's metadata |
| `r` | Read items inside the collection |
| `W` | Write the collection's metadata |
| `w` | Write items inside the collection |

`{user}` in the `collection =` regex expands to the authenticated username.

**Example — two users, one shared addressbook + one shared calendar:**

```ini
# REQUIRED — without this, clients get 403 on PROPFIND / and discovery fails.
[root]
user = .+
collection =
permissions = R

# Every user has full access to their own principal.
[owner-full-access]
user = .+
collection = {user}(/.*)?
permissions = RrWw

# user2 gets full access to user1's shared collections.
[user2-access-shared]
user = user2
collection = user1/shared-(contacts|calendar)
permissions = RrWw
```

Variations:
- **More shared collections** → extend the regex: `user1/shared-(contacts|calendar|notes)`
- **Read-only access** → drop `W` and `w`: `permissions = Rr`

## Post-installation

**Create the collections** with explicit URL slugs (DAV clients would otherwise pick UUIDs that don't match the rights regex):

```bash
./setup-collections.sh --server https://dav.<your-domain> --user user1 \
  --collections contacts:addressbook,calendar:calendar,shared-contacts:addressbook,shared-calendar:calendar

./setup-collections.sh --server https://dav.<your-domain> --user user2 \
  --collections contacts:addressbook,calendar:calendar
```

Prompts for the password (or reads `$RADICALE_PASSWORD`). Re-running is safe — existing collections return HTTP 405 and are skipped.

**Add accounts in clients.** URL pattern: `https://dav.<your-domain>/<username>/`. Username/password as set in htpasswd.

- **DAVx⁵ (Android)** — "Login with URL and user name". For user2 to see the shared collections, add a *second* account pointing at `/user1/` with user2's credentials.
- **iOS / macOS** — Settings → Accounts → Other → CalDAV/CardDAV; server `dav.<your-domain>`.
- **Thunderbird** — Address Book → New → CardDAV; same pattern for CalDAV.

## Operations

- **Backup**: enabled on `/data` via the generic chart's restic integration.
- **Add/remove user**: edit the htpasswd blob in Akeyless. ExternalSecret syncs hourly; reloader then rolls the pod.
- **Change rights**: edit the env values; ConfigMap change auto-rolls the pod.
- **Inspect rendered config**: `kubectl -n argocd exec deploy/radicale-deployment -- cat /config/rights`
- **Force reload**: `kubectl -n argocd rollout restart deploy/radicale-deployment`
- **Wipe a user's data**: `rm -rf /data/collections/<username>/` on the host (back up first).

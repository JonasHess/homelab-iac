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

### 1. Create the collections

With explicit URL slugs (DAV clients would otherwise pick UUIDs that don't match the rights regex):

```bash
./setup-collections.sh --server https://dav.<your-domain> --user user1 \
  --collections contacts:addressbook,calendar:calendar,shared-contacts:addressbook,shared-calendar:calendar

./setup-collections.sh --server https://dav.<your-domain> --user user2 \
  --collections contacts:addressbook,calendar:calendar
```

Prompts for the password (or reads `$RADICALE_PASSWORD`). Re-running is safe — existing collections return HTTP 405 and are skipped.

### 2. Connect DAVx⁵ (Android)

1. Install DAVx⁵ — free from [F-Droid](https://f-droid.org/packages/at.bitfire.davdroid/) (the Play Store price only funds development; same app).
2. Open DAVx⁵ → **+** → **"Login with URL and user name"**.
3. URL `dav.<your-domain>/<username>/`, plus the username and htpasswd password → **Login**.
4. **Contact group method** — DAVx⁵ asks how to store contact groups. Choose **"groups are per-contact categories"** (`CATEGORIES`): it's what Google's vCard export uses, and it has no separate group object that can desync. Pick "groups are separate vCards" only if you need exact interop with iOS/macOS Contacts groups. Hard to change later, so set it correctly now.
5. Tick the collections to sync (e.g. `contacts`, `calendar`, `shared-contacts`, `shared-calendar`).
6. Grant the Contacts and Calendar permissions when prompted. Disable battery optimization for DAVx⁵ so syncs run reliably.
7. Sync interval: account → ⋮ → settings (default 4h; lower it for near-realtime).

To access **shared** collections owned by another user, add a **second** DAVx⁵ account pointing at the *owner's* path (e.g. `/user1/`) with your *own* credentials — the rights file filters it to just the shared collections.

### 3. Other clients

- **iOS / macOS** — Settings → Accounts → Other → CalDAV/CardDAV; server `dav.<your-domain>`.
- **Thunderbird** — Address Book → New → CardDAV; same pattern for CalDAV.

### 4. Migrate from Google

#### Export contacts from Google

Do this on a **desktop browser** — the mobile app's export is limited:

1. Open [contacts.google.com](https://contacts.google.com) and sign in.
2. Left sidebar → **Export** (under "Fix & manage"). If it's hidden, first select contacts: use the **Selection** dropdown at the top → **All**.
3. Choose **All contacts** (or a specific label, if you only want some).
4. Format: **vCard (for iOS Contacts)** — *not* CSV; Radicale can't import CSV.
5. Click **Export** → downloads `contacts.vcf` (all contacts in one file; group membership is stored per-contact in the `CATEGORIES` field).

#### Export calendar from Google

1. Open [calendar.google.com](https://calendar.google.com) → ⚙ **Settings**.
2. **Import & export** → **Export**.
3. Downloads a `.zip` with one `.ics` per calendar — unzip it.

#### Import into Radicale

The web UI **upload creates a *new* collection** from the file — it does not import into an existing one. So do **not** pre-create (with `setup-collections.sh`) the collections you intend to fill from Google.

Via the web UI at `https://dav.<your-domain>`:

1. Log in — you land on the collection list (upload is launched from here, not from inside a collection).
2. If the target collection already exists empty (e.g. created by `setup-collections.sh`), **delete it first** (trash icon) — otherwise the upload conflicts with it.
3. Click **Upload** and select **one** `.vcf` or `.ics` file. Selecting the file auto-fills the HREF field from the filename.
4. Check the HREF field — it becomes the collection's URL slug. Any value works for a private collection; for a shared one it must be exactly `shared-contacts` / `shared-calendar`.
5. Click **Upload**. Radicale creates the collection and splits the multi-entry file into individual items.
6. Repeat **one file at a time** — selecting multiple files at once makes Radicale assign random UUID slugs and ignore the HREF field.

Clients pull the imported items on their next sync.

## Operations

- **Backup**: enabled on `/data` via the generic chart's restic integration.
- **Add/remove user**: edit the htpasswd blob in Akeyless. ExternalSecret syncs hourly; reloader then rolls the pod.
- **Change rights**: edit the env values; ConfigMap change auto-rolls the pod.
- **Inspect rendered config**: `kubectl -n argocd exec deploy/radicale-deployment -- cat /config/rights`
- **Force reload**: `kubectl -n argocd rollout restart deploy/radicale-deployment`
- **Wipe a user's data**: `rm -rf /data/collections/<username>/` on the host (back up first).

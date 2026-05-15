# Radicale

Helm chart for [Radicale](https://radicale.org/) — a lightweight CalDAV (calendar) and CardDAV (contacts) server.

Auth is handled by Radicale itself (htpasswd, bcrypt) — **no oauth2-proxy middleware**, because DAV clients (DAVx⁵, iOS/macOS Contacts, Thunderbird) speak HTTP Basic Auth directly and break behind OAuth redirects.

## Prerequisites

### 1. Create Host Directory

```bash
sudo mkdir -p /mnt/tank1/encrypted/apps/radicale/data
sudo chown -R 2999:2999 /mnt/tank1/encrypted/apps/radicale/data
```

UID `2999` = the `radicale` user inside the `tomsquest/docker-radicale` image.

### 2. Generate htpasswd Entries

Install Apache utils:

```bash
sudo apt install apache2-utils
```

Generate a **bcrypt** hash per user (matches `htpasswd_encryption = bcrypt` in the chart's config):

```bash
htpasswd -nB user1
htpasswd -nB user2
```

(`-n` prints to stdout, `-B` uses bcrypt. Omit `-b` so the password isn't echoed in shell history.)

Concatenate the two output lines into a single multi-line blob:

```
user1:$2y$05$...
user2:$2y$05$...
```

### 3. Create Akeyless Secret

Store the concatenated htpasswd blob in Akeyless at:

```
<global.akeyless.path>/radicale/HTPASSWD
```

The ExternalSecret in `apps/radicale/values.yaml` pulls this into a Kubernetes Secret mounted at `/etc/radicale-htpasswd/users` inside the pod.

### 4. DNS Record

Point `dav.<your-domain>` at your cluster ingress.

## Architecture

| Concern | Lives in |
|---|---|
| Image, ports, service, ingress, mounts | `apps/radicale/values.yaml` (env-agnostic) |
| Radicale main config file (`config`) | `apps/radicale/values.yaml` (env-agnostic — only paths, no usernames) |
| Rights file (who can access what) | `homelab_environments/<env>/values.yaml` (env-specific — names real users) |
| Data PVC `hostPath` | `homelab_environments/<env>/values.yaml` |
| Password hashes | Akeyless `/radicale/HTPASSWD` |

Mounts inside the pod:

| Path | Source | Contents |
|---|---|---|
| `/data` | PVC | Collection storage (`/data/collections/<user>/<collection>/`) |
| `/config` | ConfigMap `radicale-config-files` | `config` and `rights` files |
| `/etc/radicale-htpasswd` | Secret `radicale-htpasswd-secret` (from ExternalSecret) | `users` file (htpasswd) |

## Rights File Format

Radicale evaluates rights sections top-to-bottom. **Any matching section grants access.** Each section is INI-style:

```ini
[section-label]
user = <regex matching authenticated username>
collection = <regex matching collection URL path>
permissions = <letters>
```

### Permission letters

| Letter | Meaning |
|---|---|
| `R` | Read the collection itself (metadata, listing) |
| `r` | Read items inside the collection |
| `W` | Write the collection itself (modify metadata) |
| `w` | Write items inside the collection (create/update/delete entries) |

`RrWw` = full read/write on the collection and its items.

### `{user}` placeholder

`{user}` in the `collection =` line expands to the **currently authenticated** username. This lets one rule cover every user's own private space.

### Example: two users, one shared addressbook + one shared calendar

```ini
# Every user gets full access to their own principal and sub-collections.
[owner-full-access]
user = .+
collection = {user}(/.*)?
permissions = RrWw

# User2 can read and write User1's shared collections.
[user2-access-shared]
user = user2
collection = user1/shared-(contacts|calendar)
permissions = RrWw
```

Result:
- User1 → full access to `user1/*` (covered by section 1, including the shared ones)
- User2 → full access to `user2/*` (section 1) **and** `user1/shared-contacts`, `user1/shared-calendar` (section 2)

### How to add more shared collections

Extend the `collection` regex in the shared section. Example: also share a `notes` addressbook:

```ini
[user2-access-shared]
user = user2
collection = user1/shared-(contacts|calendar|notes)
permissions = RrWw
```

### How to add a third user

Add them to the htpasswd file in Akeyless and grant rights:

```ini
[guest-readonly-shared-calendar]
user = guest
collection = user1/shared-calendar
permissions = Rr
```

`Rr` (no `W` or `w`) = read-only.

## Post-Installation Steps

### 1. Verify the Pod Started

```bash
kubectl -n services get pods -l app=radicale
kubectl -n services logs -l app=radicale
```

You should see Radicale listening on `0.0.0.0:5232`. If it complains about missing `/config/rights`, your env values are missing the `rights` key — see Architecture above.

### 2. Create Collections via Radicale's Web UI

Open `https://dav.<your-domain>` in a browser, log in as **user1**.

The web UI lets you create collections with **explicit URL slugs**, which is required so that the rights file regex matches. Create:

- `contacts` (type: address book) — private
- `calendar` (type: calendar) — private
- `shared-contacts` (type: address book) — shared with user2
- `shared-calendar` (type: calendar) — shared with user2

Repeat for **user2**: create `contacts` and `calendar` under their account.

> ⚠️ **Why the web UI and not DAVx⁵ for setup?** DAVx⁵'s "create collection" assigns a random UUID as the URL slug (e.g. `/user1/8f2c1a.../`). That UUID won't match `user1/shared-(contacts|calendar)` in the rights file. The Radicale web UI lets you type the slug explicitly.

After this one-time setup, day-to-day use happens entirely from your clients.

### 3. Add Accounts in Clients

**DAVx⁵ (Android):**

1. Add account → "Login with URL and user name"
2. URL: `https://dav.<your-domain>/<username>/`
3. Username/password: as set in htpasswd
4. DAVx⁵ auto-discovers all collections the user has rights to (private + shared)
5. For user2 to see the shared collections, add a **second** account pointing at `https://dav.<your-domain>/user1/` with their credentials

**iOS/macOS (built-in Contacts/Calendar):**

1. Settings → Accounts → Add → Other → CalDAV/CardDAV
2. Server: `dav.<your-domain>`
3. Username/password: as set in htpasswd

**Thunderbird:**

1. Address Book → New → CardDAV
2. URL: `https://dav.<your-domain>/<username>/`
3. Same for calendar (CalDAV).

## Operational Notes

- **Backup**: enabled on the data PVC via the restic integration in the generic chart. Collection storage lives at `/data/collections/`.
- **Reloading config**: ConfigMap changes auto-roll the pod via the reloader annotation on the deployment.
- **Adding/removing users**: update the htpasswd blob in Akeyless. The ExternalSecret syncs (refreshInterval `1h` by default), then reloader rolls the pod.
- **Rotating passwords**: regenerate that user's line with `htpasswd -nB` and update the Akeyless secret.
- **Removing a user's data**: delete `/data/collections/<username>/` on the host after taking a backup.

# Backups and recovery (restic)

How this homelab backs data up with restic, and — in detail — how you get your Immich photos back
onto a **separate workstation** when the homelab itself is gone or broken.

## TL;DR

- A **daily CronJob** (`restic-backup`, namespace `argocd`, 02:00 UTC) runs a custom Python "operator" (`apps/restic/assets/restic-operations.py`) inside a self-built image (`apps/restic/Dockerfile`).
- The job **discovers `ResticBackup` custom resources** labelled `restic/backup="true"`, resolves each one's PVC → PV → **hostPath**, and runs `restic backup <hostPath>` for it.
- Those CRs are **not hand-written**: the generic chart emits one per PVC that has `backup.enabled: true` (`apps/generic/templates/backup-crd.yaml`). Turning on a backup is a one-line values change.
- Everything lands in **one shared restic repository** on S3-compatible storage (Backblaze B2), encrypted with `RESTIC_PASSWORD`. Credentials come from Akeyless via External Secrets (`restic-secret`).
- Each snapshot is tagged `backup-YYYYMMDD` and `crd:<app>-<pvc>`, e.g. `crd:immich-library`. Retention after every run: **7 daily, 3 weekly, 6 monthly**, then `prune`.
- **A restore does not need the homelab.** The repository lives in the cloud; any machine with `restic`, the repo password and the S3 keys can read it. §5 is a full walkthrough for doing exactly that on a laptop. The in-cluster `restic-restore` CronJob (§4) is the *lesser* option.
- **Verified working on 2026-07-22** — a real restore of 29 photos came back byte-identical (§6).
- ⚠️ **Immich has not been backed up since 2026-07-15** — see §6 for what broke.

---

## 1. The moving parts

| Thing | Where | Notes |
|---|---|---|
| `ResticBackup` CRD definition | `base-chart/templates/restic-backup-crd.yaml` | **Cluster-scoped**, group `backup.homelab.dev/v1` |
| CR generation | `apps/generic/templates/backup-crd.yaml` | one CR per PVC with `backup.enabled` or `backup.restore` |
| Backup CronJob | `apps/restic/templates/restic-backup-cronjob.yaml` | `restic-backup` in `argocd` |
| Restore CronJob | `apps/restic/templates/restic-restore-cronjob.yaml` | `restic-restore`, `suspend: true` |
| Logic | `apps/restic/assets/restic-operations.py` | ~540 lines, `backup` and `restore` subcommands |
| Image | `apps/restic/Dockerfile` | `python:3.14-alpine` + `apk add restic`, pushed to private ECR |
| Global excludes | `apps/restic/values.yaml` → `globalBackupRules.exclude` | rendered into ConfigMap `restic-global-excludes`, mounted at `/config` |
| RBAC | `apps/restic/templates/restic-backup-rbac.yaml` | SA `restic-backup-scanner`, cluster-wide read on PVC/PV + resticbackups |
| Secrets | `apps/restic/values.yaml` → `generic.externalSecrets.restic-secret` | Akeyless paths `/restic/*` |
| Host mounts | `homelab-environments/hess.pm/values.yaml` → `apps.restic` | `/mnt/tank0` and `/mnt/tank1` mounted into the backup pod |

### Declaring a backup

In an app's `values.yaml`:

```yaml
generic:
  persistentVolumeClaims:
    library:
      hostPath:          # set per environment
      backup:
        enabled: true
```

The generic chart turns that into:

```yaml
apiVersion: backup.homelab.dev/v1
kind: ResticBackup
metadata:
  name: immich-library          # <appName>-<pvcKey>
  labels:
    restic/backup: "true"       # what the backup job selects on
spec:
  namespace: app-immich
  pvcName: immich-library-pvc
```

Optional per-CR knobs: `include`, `exclude`, `excludeLargerThan`, `excludeCaches`, `excludeIfPresent`.

> Note: `apps/restic/README.md` documents the label as `backup.homelab.dev/enabled=true` and retention as 30/7/12. Both are stale — the code uses `restic/backup=true` and 7/3/6.

---

## 2. What a backup run actually does

`restic-backup` fires at `0 2 * * *` (UTC), `concurrencyPolicy: Forbid`, `backoffLimit: 0` (a failed pod is *not* retried).

The pod mounts `/mnt/tank0` and `/mnt/tank1` from the host at **the same paths they have on the host**. That is the crux of the design: paths resolved from the cluster API are directly usable inside the pod.

Repository configuration is assembled from the `restic-secret` env vars:

```
RESTIC_REPOSITORY = s3:$(ENDPOINT)/$(BUCKET)
AWS_ACCESS_KEY_ID = $(ACCESS_KEY_ID)
AWS_SECRET_ACCESS_KEY = $(SECRET_ACCESS_KEY)
RESTIC_PASSWORD   ← straight from the secret
```

(kubelet resolves `envFrom` before `env`, so the `$(…)` references expand correctly.)

Then, per run:

1. **`restic unlock`** — unconditionally, to clear a stale lock from a killed previous run.
2. **Load global excludes** from `/config/global-excludes.yaml`.
3. **Discover CRs**: `list_cluster_custom_object(..., label_selector="restic/backup=true")`.
4. **Resolve each CR**: read the PVC → `spec.volumeName` → read the PV → `spec.hostPath.path`. A CR whose PVC is missing, unbound, or non-hostPath is **logged and skipped**.
5. **`restic init`** if `restic snapshots` fails (first run only).
6. **Per CR, run one `restic backup`:**
   ```
   restic backup <hostPath> \
     --exclude-file <global excludes + CR excludes> \
     --tag backup-YYYYMMDD --tag crd:<cr-name> \
     --exclude-caches --exclude-if-present .nobackup \
     --one-file-system --verbose
   ```
   Missing path → that item fails, the rest continue.
7. **Maintenance**, only if ≥1 item succeeded:
   ```
   restic check --read-data-subset=5%
   restic forget --keep-daily 7 --keep-weekly 3 --keep-monthly 6 --prune
   ```
8. Exit non-zero if any item **or** maintenance failed.

Since restic's default `forget` grouping is `host,paths` and every job runs with the fixed pod hostname `restic-backup`, retention is applied **per backed-up path** — Immich's snapshots are counted separately from every other app's.

### Global excludes

Applied to *every* backup (`apps/restic/values.yaml`): `**/*.log`, `**/logs/**`, `**/tmp/**`, `**/temp/**`, `**/cache/**`, `**/.cache/**`, `**/thumbnails/**`, `**/thumbs/**`, `**/encoded-video/**`, `**/node_modules/**`, `**/target/**`, `**/build/**`, `**/dist/**`, `.DS_Store`, … These are deliberately aggressive: derived data is not worth cold-storage bytes. It also means **a restored volume is not byte-identical** to the original — thumbnails and transcodes have to be regenerated.

---

## 3. What is backed up for Immich

Immich lives in namespace `app-immich`, with three data volumes (`homelab-environments/hess.pm/values.yaml`, `apps.immich`):

| PVC | Host path | Backed up | Contents |
|---|---|---|---|
| `immich-library-pvc` | `/mnt/tank0/encrypted/apps/immich/library` | **yes** (`crd:immich-library`) | mounted at `/usr/src/app/upload` — originals, profile images, and Immich's own DB dumps |
| `immich-postgresql-pvc` | `/mnt/tank1/encrypted/apps/immich/postgresql` | **yes** (`crd:immich-postgresql`) | raw `$PGDATA` of `pgvecto-rs:pg14` |
| `immich-redis-pvc` | `/mnt/tank1/encrypted/apps/immich/redis` | **no** (`backup.enabled: false`) | job queues only — disposable |
| `immich-cli-pvc` | `/mnt/truenas/bilder` | no | read-only import source for the CLI import Job |

Inside the library snapshot the layout is:

```
/mnt/tank0/encrypted/apps/immich/library/
├── backups/     ← Immich's own daily pg_dump, ~760 MB gzipped each   ⭐
├── library/     ← THE ORIGINALS. one folder per user, then {year}/{year}-{month}/
├── profile/     ← user avatars
└── upload/      ← staging area for in-flight uploads
```

Two things worth internalising:

**a) Thumbnails and transcodes are deliberately absent.** The global excludes drop `thumbs/` and `encoded-video/`. Your originals are safe; the derived files get regenerated by Immich later.

**b) The Postgres backup is a hot file copy.** restic walks `$PGDATA` while Postgres is running and writing. It may start up and replay WAL — but don't plan around it. **The real database safety net is `backups/immich-db-backup-*.sql.gz` inside the library snapshot**, which is a proper logical dump. Confirmed present and current as of the 2026-07-15 snapshot (§6).

---

## 4. The in-cluster restore CronJob (and why §5 is usually better)

`restic-restore` is a permanently suspended CronJob. For each CR labelled `restic/restore=true` it finds the snapshot matching `RESTORE_DATE` and restores it into `/restored-data/<timestamp>/<cr-name>/…` on `restic-restoreddata-pvc`.

Four limitations:

- **It needs a working cluster** — useless in the exact disaster it exists for.
- **It resolves CRs through the live PVC/PV**, so those objects must exist before it will restore anything.
- **It is date-exact.** `RESTORE_DATE` must match a `backup-YYYYMMDD` tag that still exists (default in values is a stale `2025-05-30`).
- **The restore pod mounts only `/restored-data`**, not `/mnt/tank0`, so you cannot copy the result into place from inside it.

Use it for convenience when the cluster is healthy. For a real disaster, use §5.

---

## 5. Full recovery of the Immich library on a separate workstation

This is the important one. **Your photos are recoverable from any computer in the world** — the homelab, the node, the disks and the cluster can all be gone. What you need is three secrets and enough free disk.

This procedure was validated on macOS on 2026-07-22 (§6). It works the same on Linux.

### 5.1 What you need before you start

| # | Requirement | Details |
|---|---|---|
| 1 | **`RESTIC_PASSWORD`** | The encryption password. **Without it the backup is mathematically unrecoverable — no vendor, no support, no brute force.** |
| 2 | **S3 access key + secret key** | Backblaze B2 application key ID and key |
| 3 | **Endpoint + bucket name** | e.g. `s3.eu-central-003.backblazeb2.com` and the bucket |
| 4 | **Free disk space** | The library snapshot is **~745 GiB**. Budget **≥ 800 GiB**. |
| 5 | **`restic`** | `brew install restic` (macOS) / `apt install restic` (Debian/Ubuntu) |
| 6 | **Time and bandwidth** | ~745 GiB download. At 1 Gbit/s ≈ **2 h**; at 250 Mbit/s ≈ **7 h**; at 100 Mbit/s ≈ **17 h** |

**Where the three secrets live today:** Akeyless, at `/restic/RESTIC_PASSWORD`, `/restic/S3_ACCESS_KEY_ID`, `/restic/S3_SECRET_ACCESS_KEY`, `/restic/S3_ENDPOINT`, `/restic/S3_BUCKET`. They are mirrored into the cluster as the `restic-secret` Secret in namespace `argocd`.

> **🔴 Do this before you ever need it.** If the homelab is down you cannot use `kubectl`, and if Akeyless is also unreachable you have nothing. Keep a printed or offline copy (password manager on your phone, a piece of paper in a drawer) of all five values. This is the single biggest risk in the whole setup — everything else is recoverable, a lost password is not.

**A note on the target disk:** restore onto a Linux filesystem (ext4/xfs/btrfs), APFS, or an APFS external drive. Avoid exFAT/FAT (no ownership, restic will warn on every file) and be aware that **the default macOS APFS is case-insensitive** — if two photos differ only in capitalisation, one overwrites the other. For a full fidelity restore on macOS, create a **case-sensitive** APFS volume in Disk Utility.

### 5.2 Step 1 — Install restic and set up access

```bash
brew install restic     # macOS
restic version          # verified with 0.19.1
```

Now make the credentials available. **Option A — the homelab still runs** (pull them from the cluster):

Save this as `rst.sh`, `chmod +x rst.sh`. It fetches the secrets fresh on every call so nothing is written to disk, and it refuses any command that could damage the repository:

```bash
#!/bin/bash
# Read-only restic wrapper. Pulls creds from the hess.pm cluster secret at call time.
set -euo pipefail

case "${1:-}" in
  forget|prune|unlock|init|remove|rewrite|repair|migrate|copy|key|tag|backup)
    echo "REFUSED: '$1' can modify the repository. Read-only commands only." >&2
    exit 64
    ;;
esac

SEC_JSON="$(kubectl --context=config.d-hess.pm -n argocd get secret restic-secret -o json)"
get() { printf '%s' "$SEC_JSON" | jq -r ".data[\"$1\"]" | base64 -d; }

export AWS_ACCESS_KEY_ID="$(get ACCESS_KEY_ID)"
export AWS_SECRET_ACCESS_KEY="$(get SECRET_ACCESS_KEY)"
export RESTIC_PASSWORD="$(get RESTIC_PASSWORD)"
export RESTIC_REPOSITORY="s3:$(get ENDPOINT)/$(get BUCKET)"

exec restic "$@"
```

**Option B — the homelab is gone** (type the secrets in by hand from your offline copy):

```bash
export RESTIC_REPOSITORY="s3:s3.eu-central-003.backblazeb2.com/<bucket-name>"
export AWS_ACCESS_KEY_ID="<b2 application key id>"
export AWS_SECRET_ACCESS_KEY="<b2 application key>"
read -rsp "restic password: " RESTIC_PASSWORD; export RESTIC_PASSWORD; echo
```

`read -rsp` keeps the password out of your shell history. From here on, `./rst.sh <cmd>` and plain `restic <cmd>` are interchangeable — use whichever matches the option you picked.

**Always pass `--no-lock`.** It stops your laptop from writing lock files into the repository, so you can never interfere with a running backup job. Every command below already includes it.

### 5.3 Step 2 — Find the snapshot you want

```bash
./rst.sh snapshots --tag crd:immich-library --no-lock
```

Real output from 2026-07-22:

```
ID        Time                 Host           Tags                                Paths                                     Size
e1bd4129  2026-02-28 03:01:48  restic-backup  backup-20260228,crd:immich-library  /mnt/tank0/.../library  735.994 GiB
...
badcc605  2026-07-13 04:01:45  restic-backup  backup-20260713,crd:immich-library  /mnt/tank0/.../library  744.628 GiB
a16ff00a  2026-07-14 04:01:31  restic-backup  backup-20260714,crd:immich-library  /mnt/tank0/.../library  744.654 GiB
44d9dd11  2026-07-15 04:01:45  restic-backup  backup-20260715,crd:immich-library  /mnt/tank0/.../library  744.782 GiB
```

How to read it:

- **ID** — the short snapshot ID you pass to every later command (`44d9dd11`).
- **Time** — when the backup ran, in *your local* timezone.
- **Size** — total size of the files in that snapshot. It should grow gently over time. **A sudden drop is a red flag**: it means files disappeared from the source before the backup ran. Pick an older snapshot in that case.
- Usually you want **the newest** one. Pick an older one if you are recovering from something that corrupted or deleted data *before* the last backup (accidental mass delete, ransomware, a bad Immich upgrade).

Check what a restore will cost you in disk space:

```bash
./rst.sh stats 44d9dd11 --mode restore-size --no-lock
```

### 5.4 Step 3 — Look before you leap

Never start a 745 GiB download without confirming the snapshot has what you expect.

```bash
# top level of the snapshot
./rst.sh ls 44d9dd11 --no-lock /mnt/tank0/encrypted/apps/immich/library

# which users exist
./rst.sh ls 44d9dd11 --no-lock /mnt/tank0/encrypted/apps/immich/library/library

# a specific month, with sizes and dates
./rst.sh ls -l 44d9dd11 --no-lock \
  /mnt/tank0/encrypted/apps/immich/library/library/admin/2026/2026-07 | head -20

# is the database dump there?
./rst.sh ls -l 44d9dd11 --no-lock \
  /mnt/tank0/encrypted/apps/immich/library/backups | tail -5
```

You can also hunt for one specific photo across every snapshot:

```bash
./rst.sh find --no-lock '20260701_005729.jpg'
```

### 5.5 Step 4 — Do a 10-file rehearsal first

**Always do this.** It takes 30 seconds and proves the password, the network, the credentials and the data are all good — *before* you commit to hours of downloading.

```bash
./rst.sh restore 44d9dd11 --no-lock \
  --target ./restore-test \
  --include '/mnt/tank0/encrypted/apps/immich/library/library/admin/2026/2026-07/202607*.jpg'
```

Then verify what came out (macOS):

```bash
cd ./restore-test/mnt/tank0/encrypted/apps/immich/library/library/admin/2026/2026-07

file *.jpg | head                       # should say "JPEG image data, Exif standard"
sips -g pixelWidth -g pixelHeight *.jpg | head
open .                                  # look at them with your own eyes
```

A stricter check — decode every file completely, not just its header:

```bash
mkdir -p /tmp/decode-check
for f in *.jpg; do
  sips -s format png "$f" --out "/tmp/decode-check/$f.png" >/dev/null 2>&1 \
    || echo "DECODE FAILED: $f"
done
rm -rf /tmp/decode-check
```

Silence means every file is intact. On Linux use `jpeginfo -c *.jpg` instead.

### 5.6 Step 5 — Restore the whole library

Now the real thing.

```bash
# macOS: stop the machine sleeping mid-download
caffeinate -i ./rst.sh restore 44d9dd11 --no-lock \
  --target /Volumes/BigDisk/immich-restore \
  --verify
```

What the flags do:

- `--target <dir>` — everything lands under here. restic **recreates the full absolute path**, so your photos end up at:
  ```
  /Volumes/BigDisk/immich-restore/mnt/tank0/encrypted/apps/immich/library/library/admin/2026/…
  ```
  That looks odd but is correct and makes the origin unambiguous.
- `--verify` — re-reads every restored file and checks it against the hash stored in the repository. Slower, worth it.
- `--no-lock` — never write to the repository.

**If you don't need everything**, restore selectively — much faster:

```bash
# just the originals, skip the 760 MB-per-day database dumps and the upload staging dir
--include '/mnt/tank0/encrypted/apps/immich/library/library/**'

# just one user
--include '/mnt/tank0/encrypted/apps/immich/library/library/admin/**'

# just one year
--include '/mnt/tank0/encrypted/apps/immich/library/library/admin/2026/**'
```

**If it gets interrupted** — network drop, laptop sleep, Ctrl-C — just run the exact same command again. restic picks up where it stopped:

```bash
./rst.sh restore 44d9dd11 --no-lock \
  --target /Volumes/BigDisk/immich-restore \
  --overwrite if-changed
```

`--overwrite if-changed` makes restic skip files that are already on disk with the right size and timestamp, so a resumed run costs minutes instead of hours.

**Doing it in chunks** is a good idea on a flaky connection — restore year by year, verifying as you go:

```bash
for y in 2019 2020 2021 2022 2023 2024 2025 2026; do
  echo "=== $y ==="
  ./rst.sh restore 44d9dd11 --no-lock \
    --target /Volumes/BigDisk/immich-restore \
    --overwrite if-changed \
    --include "/mnt/tank0/encrypted/apps/immich/library/library/*/$y/**"
done
```

### 5.7 Step 6 — Verify the result

```bash
cd /Volumes/BigDisk/immich-restore/mnt/tank0/encrypted/apps/immich/library

du -sh library/                 # should land near the snapshot size
find library -type f | wc -l    # how many originals came back
find library -type f -size -1k  # suspiciously tiny files — should print nothing
```

Cross-check the count against the repository itself:

```bash
./rst.sh ls -r 44d9dd11 --no-lock \
  /mnt/tank0/encrypted/apps/immich/library/library | grep -c '\.'
```

The two numbers should match. Then open a handful of photos from different years by hand. **A backup you have not looked at is a rumour, not a backup.**

### 5.8 Step 7 — What you now have, and what to do with it

You have **all your original photos and videos**, in a plain, boring folder tree:

```
library/<user>/<year>/<year>-<month>/<filename>
```

No Immich, no database, no special tooling needed to read them — any file manager, Photos app or backup tool can take it from here. **That alone means your memories are safe.**

What is *not* in there: thumbnails, transcoded videos (excluded by design), albums, faces, shared links, and other metadata — those live in the database.

To rebuild a *working Immich* rather than just recover the files, you have two routes:

**Route 1 — full restore (keeps albums, faces, everything).** Bring up Immich on new hardware, copy this tree into the new `UPLOAD_LOCATION`, then load the matching database dump you restored alongside it:

```bash
ls backups/         # immich-db-backup-20260715T020000-v3.0.2-pg14.19.sql.gz
gunzip -c backups/immich-db-backup-20260715T020000-v3.0.2-pg14.19.sql.gz \
  | psql -U <user> -d postgres
```

Use the dump from **the same snapshot** as the files, so database and disk agree. Restore into the same Postgres major version (**pg14**, with the `pgvecto-rs` extension the chart pins), and start an Immich version close to the dump's (`v3.0.2` above) before letting it migrate forward. Then in **Administration → Jobs** run **Thumbnail Generation → All** and **Video Conversion → All** to rebuild what the excludes left out.

**Route 2 — start fresh (simplest, loses albums/faces).** Point a new Immich at this folder as an **external library**, or re-upload it with `immich-cli`. Immich re-reads the EXIF, so dates, locations and camera info all survive. You lose albums, favourites, face names and shared links.

---

## 6. Verification log

### 2026-07-22 — restore test: **PASSED**

Performed from a macOS workstation with `restic 0.19.1`, entirely outside the cluster, against the live B2 repository.

| Check | Result |
|---|---|
| Repository reachable, password correct | ✅ decrypted and listed |
| `crd:immich-library` snapshots | ✅ 13 — monthlies back to 2026-02-28, dailies 07-09 → **07-15** |
| Newest library snapshot | `44d9dd11`, 2026-07-15, **744.782 GiB**, size trend steadily rising |
| `crd:immich-postgresql` snapshots | ✅ 13, newest `e41554d4`, 2026-07-15, 6.901 GiB |
| Test restore | ✅ **29 files / 78 MiB in 10 seconds** |
| Byte-exactness | ✅ restored sizes match snapshot metadata exactly (1852568 / 2835969 / 4052068) |
| Full JPEG decode | ✅ 29 / 29 decoded, 0 failures |
| EXIF intact | ✅ `Galaxy Z Fold5`, capture timestamp matches filename |
| Visual check | ✅ opened in Preview, correct image |
| Immich DB dumps inside library snapshot | ✅ daily `immich-db-backup-*.sql.gz`, ~760 MB, through 2026-07-15 |
| Repository integrity | ✅ `restic check --read-data-subset=5%` passed during the 2026-07-21 job |

**Conclusion: photos up to 2026-07-15 are recoverable with high confidence.** Not verified: the remaining ~745 GiB was not downloaded, and a full `restic check --read-data` has never been run.

### 2026-07-22 — ⚠️ backup outage: Immich unprotected since 2026-07-15

| When | What happened |
|---|---|
| **07-15 02:00** | Last successful Immich backup (`44d9dd11` / `e41554d4`) |
| **07-16 02:01** | Job hung while scanning `/mnt/tank0/encrypted/apps/immich/library` — ran **5 days** without finishing |
| **07-17 → 07-20** | **No backups at all.** `concurrencyPolicy: Forbid` meant the stuck job blocked every scheduled run |
| **07-21 21:15** | Run finally proceeded: **Success 9, Failed 12**. Every failure was a path under `encrypted/`; every success was under `unencrypted/`. Both Immich CRs failed with `Backup path does not exist` |

The recovery point is frozen at 2026-07-15 until the `encrypted/` paths are visible to the backup pod again. Note the pattern — **`unencrypted/` worked, `encrypted/` did not** — which points at the encrypted mounts not being present inside the backup pod's `/mnt/tank0` and `/mnt/tank1` bind mounts, rather than at restic or S3.

Retention is not an immediate threat: `--keep-daily 7` preserves the seven most recent *daily snapshots of that path* (07-09 → 07-15) regardless of how old they get, and the monthlies reach back to 2026-02-28.

---

## 7. Known gaps and risks

Ordered roughly by how much they would hurt.

1. **`RESTIC_PASSWORD` lives only in Akeyless.** Lose it and every snapshot is permanently unreadable. Keep an offline copy of the password *and* the S3 credentials — see §5.1.
2. **No alerting on failure.** The job exits non-zero and that is it — no retry, no notification. Immich silently stopped being backed up for a week and nothing said a word. An alert on `kube_job_status_failed{job_name=~"restic-backup.*"}`, plus one on "no successful run in 48 h", would have caught it on day one.
3. **`concurrencyPolicy: Forbid` + a hanging job is a silent outage.** One stuck scan blocked five nights of backups for *every* application. There is no timeout on the restic invocation (`run_restic_command` defaults to a 3600 s timeout, but the streaming path passes it only to `process.wait()` after the read loop, so a stalled read blocks forever). An `activeDeadlineSeconds` on the Job would bound this.
4. **The Postgres backup is a hot file copy** (§3b). The real safety net is Immich's internal dump inside the library snapshot — which nothing in this repo asserts, configures, or monitors. If Immich stops writing dumps, the backup silently degrades.
5. **Retention caps recovery at ~6 months**, and only on the dates the weekly/monthly rules kept.
6. **`restic check --read-data-subset=5%` verifies bytes, not restorability.** Before 2026-07-22 no end-to-end restore had ever been performed. Repeat §5.5 quarterly.
7. **The in-cluster restore CronJob is the wrong tool for a real disaster** (§4) and its default `RESTORE_DATE` is a stale `2025-05-30`.
8. **Restores are not byte-identical**: the aggressive global excludes drop derived data, and any real directory named `cache`, `logs`, `build`, `dist`, `target`, or `tmp` is silently skipped for *every* app.
9. **`--one-file-system`** means nested mounts under a backed-up path are skipped without warning — directly relevant to the `encrypted`/`unencrypted` layout under `/mnt/tank0` and `/mnt/tank1`.
10. **`apps/restic/README.md` is out of date** (label name, retention numbers). Trust the Python script and this document.
11. **`backrest` runs in parallel** with its configuration stored in `/mnt/tank1/encrypted/apps/backrest/data`, outside git. Whatever it protects is not reproducible from this repo — and if it points at the same bucket, its retention interacts with the CronJob's `forget --prune`.

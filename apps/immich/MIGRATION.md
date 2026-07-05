# Immich v3 migration runbook — pgvecto.rs → VectorChord

## Why
Immich v3 removed support for the pgvecto.rs (`vectors`) Postgres extension. The DB
previously ran `tensorchord/pgvecto-rs:pg14-v0.2.0` (extension `vectors` 0.2.0), so the
v3.0.1 server pod crashloops with `No vector extension found`. Immich v3 needs `vector`
(pgvector) or `vchord` (VectorChord).

The official Immich Postgres image `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`
bundles **both** VectorChord and pgvecto.rs 0.2.0. That makes it:
- backward-compatible with the v2.7.5 server (still uses `vectors`), and
- able to auto-migrate `vectors` → `vchord` in place the first time v3 starts.

Same major version (pg14 → pg14), so the existing data PVC is used as-is — no pg dump/restore
for the version bump itself.

## Chart changes
- `templates/immich-postgres.yaml`
  - image → `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`
  - removed the custom `postgres -c shared_preload_libraries=vectors.so … search_path … vectors …`
    args (the image bakes the correct preload for both extensions; setting `vectors.so`
    manually would stop VectorChord from loading). This also drops the previous perf tuning
    (`max_wal_size=2GB`, `shared_buffers=512MB`, `wal_compression=on`). Re-add later as extra
    `-c` flags if wanted — but never re-add `shared_preload_libraries` or `search_path`.
  - added a 128Mi `/dev/shm` emptyDir (VectorChord index builds).
- `templates/immich.yaml` — server + machine-learning → `v3.0.1`
- `templates/immich-cli.yaml` — cli → `3.0.1`

The change splits by file into two phases so it can be pushed and verified in two safe steps.

## 0. Pre-flight — back up the DB first
ArgoCD has autosync + selfHeal on `argocd-app-immich`, so a push deploys automatically and
the v3 migration is one-way. Take a logical backup before pushing Phase B.

```bash
POD=$(kubectl -n app-immich get pod -l component=database -o name | head -1)
kubectl -n app-immich exec "$POD" -- \
  bash -c 'pg_dumpall -U "$POSTGRES_USER" --clean --if-exists' \
  > immich-db-backup-pre-vchord.sql
# sanity: file should be non-trivial in size and end with a normal SQL statement
ls -lh immich-db-backup-pre-vchord.sql
```

The `postgresql` PVC also has restic backup enabled, but the SQL dump is the fast, verifiable
rollback path if anything goes wrong.

## 1. Phase A — swap the Postgres image (still on v2.7.5)
De-risks the image swap independently of the app upgrade. The migration image is backward
compatible, so the running v2.7.5 server keeps working against it.

```bash
git add apps/immich/templates/immich-postgres.yaml
git commit -m "immich: switch Postgres to VectorChord migration image"
git push
```

Wait for ArgoCD to recreate the postgres pod (Deployment strategy is `Recreate`) and verify:

```bash
kubectl -n app-immich get pods -l component=database
kubectl -n app-immich exec "$POD" -- \
  psql -U immich -d immich -tAc \
  "SELECT name, installed_version FROM pg_available_extensions WHERE name IN ('vectors','vchord','vector');"
# expect: vectors installed (0.2.0); vchord + vector available (not yet installed)
```

Confirm Immich itself is still healthy (photos load, no server restarts) before Phase B.

## 2. Phase B — bump Immich to v3.0.1 (triggers auto-migration)
```bash
git add apps/immich/templates/immich.yaml apps/immich/templates/immich-cli.yaml
git commit -m "immich: upgrade server/ML/CLI to v3.0.1 on VectorChord"
git push
```

On startup v3 migrates `vectors` → `vchord` (seconds to minutes). Watch it:

```bash
kubectl -n app-immich rollout status deploy/immich-app-server --timeout=15m
kubectl -n app-immich logs -f deploy/immich-app-server -c server
kubectl -n app-immich exec "$POD" -- \
  psql -U immich -d immich -tAc \
  "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vchord','vectors','vector');"
# expect vchord present; the old vectors extension is dropped once migration completes
```

## 3. Verify
- `kubectl -n app-immich get pods` → server, machine-learning, postgres, redis all `1/1`,
  server `RESTARTS 0`.
- Immich UI loads; open a photo; smart/CLIP search returns results (exercises the vector index).
- `argocd-app-immich` shows `Synced / Healthy`.

## 4. Rollback
If Phase A misbehaves: `git revert` the Phase A commit and push — back to pgvecto.rs image,
data untouched.

If Phase B / the migration fails: the safe path is a **full restore**, because the migration
mutates the vector data.
```bash
# revert both phases in git, push (returns manifests to v2.7.5 + pgvecto.rs image)
# then, if the vector data was already migrated, restore the dump onto the pgvecto.rs image:
kubectl -n app-immich exec -i "$POD" -- \
  bash -c 'psql -U "$POSTGRES_USER" -d postgres' < immich-db-backup-pre-vchord.sql
```

## Notes
- Verify the image tag `14-vectorchord0.4.3-pgvectors0.2.0` is still the current one in the
  Immich upgrade docs before pushing — Immich bumps the VectorChord/pgvectors versions in the
  tag over time. The `pg14` / `pgvectors0.2.0` parts must match the current setup (they do).

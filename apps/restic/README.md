# Restic backups (ResticBackup CRs)

Nightly restic backups of PVC data to S3, configured declaratively with one `ResticBackup` custom resource per volume. The full picture (what runs when, the Immich layout, a tested workstation restore, known gaps) is in `docs/backup-and-recovery.md`; this file covers the chart.

## How it works

1. An app marks a volume for backup in its generic chart values; the generic chart renders a `ResticBackup` CR for it (`apps/generic/templates/backup-crd.yaml`).
2. The CronJob `restic-backup` (02:00 by default, `cronjob.schedule`) runs `assets/restic-operations.py backup restic/backup=true`.
3. The script lists every `ResticBackup` with the label `restic/backup=true`, resolves each PVC to its PV's hostPath, and backs that path up from the host mounts of the backup pod.
4. After the backups it runs `restic check --read-data-subset=5%` and `restic forget --keep-daily 7 --keep-weekly 3 --keep-monthly 6 --prune`.

The alerts in `templates/restic-alerts.yaml` fire when the CronJob (or the database copies it relies on) has not succeeded for 36 hours, or has never succeeded.

## Marking a volume for backup

In the app's `values.yaml`, on a `pvcMounts` entry or a `persistentVolumeClaims` entry:

```yaml
generic:
  deployment:
    pvcMounts:
      data:
        mountPath: /data
        hostPath: ~
        backup:
          enabled: true            # label restic/backup: "true"
          exclude: ["**/thumbs/**"] # optional, on top of the global excludes
          excludeLargerThan: "1G"   # optional
          excludeCaches: true       # optional, default true
          excludeIfPresent: .nobackup # optional, default .nobackup
```

Hand-written CRs work too, as long as they carry the label `restic/backup: "true"`; `templates/example-backup-crds.yaml` has three (`examples.enabled: true` renders them).

Global excludes (logs, caches, temp, build output) live in `globalBackupRules.exclude` in `values.yaml` and apply to every backup. A real directory named `cache`, `logs`, `tmp`, ... is skipped in every app.

## Restoring

For a real disaster, restore on a workstation as described in `docs/backup-and-recovery.md` (section 5). The in-cluster restore below writes into a separate volume and is meant for single apps:

1. Set `backup.restore: true` on the volume (label `restic/restore: "true"`) and `restoreCronjob.date` (`YYYY-MM-DD`) in the restic values, then push.
2. Start the restore: `kubectl --context <ctx> -n argocd create job --from=cronjob/restic-restore restore-$(date +%Y%m%d)`.
3. The data lands in the `restoreddata` volume, mounted at `/restored-data` in the restore pod.
4. Remove `backup.restore` again afterwards.

## Useful commands

```bash
kubectl --context <ctx> get resticbackups                      # every CR (cluster-scoped)
kubectl --context <ctx> get resticbackups -l restic/backup=true
kubectl --context <ctx> -n argocd create job --from=cronjob/restic-backup backup-manual-$(date +%Y%m%d)
kubectl --context <ctx> -n argocd logs job/<job-name>
```

## Troubleshooting

- **Volume not backed up**: the CR is missing the `restic/backup: "true"` label, or `backup.enabled` is not set on that volume.
- **PVC resolution failed**: the PVC does not exist, is not bound, or its PV has no `hostPath`.
- **Path not found**: the resolved hostPath is not below one of the backup pod's host mounts (`cronjob.pvcMounts`, set per environment).
- **Permission denied**: check the `restic-backup-scanner` ServiceAccount and its ClusterRole in `templates/restic-backup-rbac.yaml`.

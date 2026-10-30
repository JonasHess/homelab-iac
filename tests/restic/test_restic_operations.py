def backed_up(ops):
    """Names of the ResticBackups a `restic backup` call was made for."""
    return [a[a.index("--tag") + 3].removeprefix("crd:") for a in ops.restic_calls if a[0] == "backup"]


def test_backup_of_every_resolvable_volume_succeeds(ops, cluster, tmp_path):
    cluster.add_backup("app-data", tmp_path)

    assert ops.main("backup", "restic/backup=true") == 0
    assert backed_up(ops) == ["app-data"]


def test_backup_fails_when_no_resticbackup_matches(ops):
    # Every deployed app with backup.enabled has a ResticBackup, so an empty result
    # means the CRs or their labels are gone, not that there is nothing to back up.
    assert ops.main("backup", "restic/backup=true") == 1


def test_restore_with_nothing_selected_is_not_an_error(ops):
    # Restore only acts on ResticBackups labelled for it; none is the normal state.
    assert ops.main("restore", "restic/restore=true") == 0


def test_backup_fails_when_a_volume_cannot_be_resolved(ops, cluster, tmp_path):
    cluster.add_backup("app-data", tmp_path)
    cluster.add_backup("unbound", tmp_path, bound=False)

    assert ops.main("backup", "restic/backup=true") == 1


def test_unresolvable_volume_does_not_stop_the_others(ops, cluster, tmp_path):
    cluster.add_backup("unbound", tmp_path, bound=False)
    cluster.add_backup("app-data", tmp_path)

    ops.main("backup", "restic/backup=true")

    assert backed_up(ops) == ["app-data"]

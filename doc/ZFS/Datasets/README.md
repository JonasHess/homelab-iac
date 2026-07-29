# ZFS pool and dataset layout

The `hess.pm` host currently uses three automatically imported pools:

| Pool | Layout | Mountpoint | Purpose |
|---|---|---|---|
| `tank1` | SSD mirror | `/mnt/tank1` | Application configuration and other SSD-backed data |
| `hdd-z1` | RAIDZ1 | `/mnt/hdd-z1` | Replaceable media |
| `hdd-z2` | RAIDZ2 | `/mnt/hdd-z2` | Immich and irreplaceable data |

The old `tank0`, `tank2`, and `tank3` pool names are retired.

## Encrypted datasets

Each pool has a passphrase-protected encryption root:

```text
tank1/encrypted
hdd-z1/encrypted
hdd-z2/encrypted
```

Their `keylocation` is `prompt`. Pool import is automatic, but key loading is
deliberately manual. No passphrase is stored on the host.

After a reboot, unlock and mount the encrypted datasets:

```bash
sudo zfs load-key tank1/encrypted
sudo zfs load-key hdd-z1/encrypted
sudo zfs load-key hdd-z2/encrypted
sudo zfs mount -a
```

Start or restart MicroK8s only after the encrypted datasets are mounted.
Containers that started earlier can retain bind mounts to the empty underlying
directories even after ZFS is mounted.

## Automatic pool import

`/etc/zfs/zpool.cache` contains only:

```text
tank1
hdd-z1
hdd-z2
```

The following units are enabled:

```text
zfs-import-cache.service
zfs-import.target
zfs-mount.service
zfs.target
```

The exported `shuttle` cold-copy pool is intentionally absent from the cache.

Verify the boot configuration:

```bash
sudo zdb -C -U /etc/zfs/zpool.cache | grep -E '^[[:space:]]*name:'
systemctl is-enabled zfs-import-cache.service zfs-import.target zfs-mount.service zfs.target
```

## Media dataset

Media has its own encrypted child dataset:

```text
hdd-z1/encrypted/media
```

It mounts at `/mnt/hdd-z1/encrypted/media`, inherits encryption from
`hdd-z1/encrypted`, and uses:

```text
compression=lz4
atime=off
recordsize=1M
xattr=sa
dnodesize=auto
```

Making media a separate dataset allows zrepl to exclude it recursively while
still snapshotting the rest of `hdd-z1`.

Verify the current layout:

```bash
zpool list
zfs list -o name,mounted,mountpoint,encryptionroot,keystatus
zfs get compression,atime,recordsize,xattr,dnodesize hdd-z1/encrypted/media
```

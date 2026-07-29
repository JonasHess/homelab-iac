# ZFS snapshots with zrepl

zrepl creates local snapshots for `tank1`, `hdd-z1`, and `hdd-z2`.
The replaceable-media dataset `hdd-z1/encrypted/media` is excluded recursively.

The live configuration is `/etc/zrepl/zrepl.yml`; the repository copy is
[`zrepl.yml`](zrepl.yml).

## Install zrepl

```bash
(
set -ex
zrepl_apt_key_url=https://zrepl.cschwarz.com/apt/apt-key.asc
zrepl_apt_key_dst=/usr/share/keyrings/zrepl.gpg
zrepl_apt_repo_file=/etc/apt/sources.list.d/zrepl.list

# Install dependencies for subsequent commands
sudo apt update && sudo apt install curl gnupg lsb-release

# Deploy the zrepl apt key.
curl -fsSL "$zrepl_apt_key_url" | tee | gpg --dearmor | sudo tee "$zrepl_apt_key_dst" > /dev/null

# Add the zrepl apt repo.
ARCH="$(dpkg --print-architecture)"
CODENAME="$(lsb_release -i -s | tr '[:upper:]' '[:lower:]') $(lsb_release -c -s | tr '[:upper:]' '[:lower:]')"
echo "Using Distro and Codename: $CODENAME"
echo "deb [arch=$ARCH signed-by=$zrepl_apt_key_dst] https://zrepl.cschwarz.com/apt/$CODENAME main" | sudo tee "$zrepl_apt_repo_file" > /dev/null

# Update apt repos.
sudo apt update
)
```

If automatic distribution detection produces a repository 404, use:

```text
CODENAME="ubuntu noble"
```

```bash
sudo apt-get install zrepl
```

## Snapshot selection

The current selector is:

```yaml
filesystems: {
  "tank1<": true,
  "hdd-z1<": true,
  "hdd-z2<": true,
  "hdd-z1/encrypted/media<": false
}
```

The `<` suffix selects a dataset and its descendants. The explicit `false`
entry excludes media and all descendants from the otherwise recursive
`hdd-z1` selection.

Snapshots are created every 15 minutes with the `zrepl_` prefix. Retention is:

```text
1x1h(keep=all) | 24x1h | 35x1d | 6x30d
```

Snapshots without the `zrepl_` prefix are retained.

## Deploy the configuration

```bash
ssh root@192.168.1.3 "mkdir -p /etc/zrepl"
scp ./zrepl.yml root@192.168.1.3:/etc/zrepl/zrepl.yml
```

```bash
ssh root@192.168.1.3 "zrepl configcheck"
```

Enable and start the service:

```bash
ssh root@192.168.1.3 \
  "systemctl unmask zrepl.service && systemctl enable --now zrepl.service"
```

## Verification

```bash
systemctl is-enabled zrepl.service
systemctl is-active zrepl.service
zrepl status

# Included datasets should have recent snapshots.
zfs list -t snapshot -r tank1
zfs list -t snapshot -r hdd-z1
zfs list -t snapshot -r hdd-z2

# Media must remain excluded.
zfs list -H -t snapshot -r hdd-z1/encrypted/media -o name |
  grep '@zrepl_'  # expected: no output
```

At boot, zrepl starts automatically. ZFS pool import is also automatic, but
encrypted dataset keys remain prompt-only and must be loaded manually. See
[`../Datasets/README.md`](../Datasets/README.md) for the reboot procedure.

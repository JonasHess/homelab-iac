# Creating Raidz1
```bash
sudo zpool create -m /mnt/tank1 tank1 raidz /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413321T /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413330N /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413331K /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413334T
```

## Creating datasets
```bash
zfs create -o dedup=on -o compression=on -o atime=off -o encryption=aes-256-gcm -o keyformat=passphrase tank1/encrypted
zfs create -o dedup=on -o compression=on -o atime=off tank1/unencrypted
```

## Creating snapshots
Install zrepl
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

## ZRepl config
copy the configuration to /etc/zrepl/zrepl.yml

```yaml
# This config serves as an example for a local zrepl installation that
# backups the entire zpool `system` to `backuppool/zrepl/sink`
#
# The requirements covered by this setup are described in the zrepl documentation's
# quick start section which inlines this example.
#
# CUSTOMIZATIONS YOU WILL LIKELY WANT TO APPLY:
# - adjust the name of the production pool `system` in the `filesystems` filter of jobs `snapjob` and `push_to_drive`
# - adjust the name of the backup pool `backuppool` in the `backuppool_sink` job
# - adjust the occurences of `myhostname` to the name of the system you are backing up (cannot be easily changed once you start replicating)
# - make sure the `zrepl_` prefix is not being used by any other zfs tools you might have installed (it likely isn't)

jobs:

# this job takes care of snapshot creation + pruning
- name: snapjob
  type: snap
  filesystems: {
    "tank1<": true,
  }
  # create snapshots with prefix `zrepl_` every 15 minutes
  snapshotting:
    type: periodic
    interval: 15m
    prefix: zrepl_
  pruning:
    keep:
  # fade-out scheme for snapshots starting with `zrepl_`
  # - keep all created in the last hour
  # - then destroy snapshots such that we keep 24 each 1 hour apart
  # - then destroy snapshots such that we keep 14 each 1 day apart
  # - then destroy all older snapshots
    - type: grid
      grid: 1x1h(keep=all) | 24x1h | 35x1d | 6x30d
      regex: "^zrepl_.*"
  # keep all snapshots that don't have the `zrepl_` prefix
    - type: regex
      negate: true
      regex: "^zrepl_.*"
```


## Install micro k8s
```bash
snap install microk8s --classic
microk8s enable dns
microk8s enable metallb:192.168.0.80-192.168.0.90
# microk8s enable gpu
microk8s start
```

## Create kubectl context
```bash
mkdir /home/homeserver/.kube/
usermod -a -G microk8s homeserver
chown -R homeserver ~/.kube

microk8s config > ~/.kube/config
scp homeserver@192.168.1.3:/home/homeserver/.kube/config ~/.kube/config
chmod 600 ~/.kube/config
```

## Refresh certificates (e.g. after IP change)
```bash
sudo microk8s refresh-certs --cert ca.crt
```
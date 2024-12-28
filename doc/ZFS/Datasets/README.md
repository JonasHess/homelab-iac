# Creating Raidz1
```bash
sudo zpool create -m /mnt/tank1 tank1 raidz /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413321T /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413330N /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413331K /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S75CNX0X413334T
```

## Creating datasets
```bash
zfs create -o dedup=on -o compression=on -o atime=off -o encryption=aes-256-gcm -o keyformat=passphrase tank1/encrypted
zfs create -o dedup=on -o compression=on -o atime=off tank1/unencrypted
zfs create -o dedup=on -o compression=zstd-3 -o atime=off tank0/unencrypted


zfs create -o atime=off -o encryption=aes-256-gcm -o keyformat=passphrase tank0/encrypted

sudo zpool create -m /mnt/tank0 tank0 raidz /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_11B0A074FVGG /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_51B0A07XFVGG /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_51B0A0FEFVGG /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_X1C0A0B4FVGG /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_X1K0A2ZGFWTG

sudo wipefs -a /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_11B0A074FVGG
sudo wipefs -a /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_51B0A07XFVGG
sudo wipefs -a /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_51B0A0FEFVGG
sudo wipefs -a /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_X1C0A0B4FVGG
sudo wipefs -a /dev/disk/by-id/ata-TOSHIBA_MG08ACA16TE_X1K0A2ZGFWTG
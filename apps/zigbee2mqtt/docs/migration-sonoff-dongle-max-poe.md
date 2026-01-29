# Migration Guide: Z-Stack USB Dongle to SONOFF Dongle Max (Ember/PoE)

## Overview

This guide covers migrating from a **Z-Stack USB Zigbee dongle** (e.g. SONOFF Zigbee 3.0 USB
Dongle Plus, CC2652) to a **SONOFF Zigbee/Thread PoE Dongle Max** (EFR32MG24 / Ember)
connected via **Ethernet (PoE)**.

This is a **Z-Stack to Ember** firmware stack migration combined with a **USB to network**
connection change. Because the firmware stacks are fundamentally different, a specific
sequence of steps is required to preserve device pairings.

**Scope:** This migration is performed per-environment. The zigbee2mqtt Helm chart and its
base `values.yaml` are not modified. All changes happen in the environment-specific values
file (e.g. `homelab-environments/<env>/values.yaml`).

## What Changes

| Aspect | Before | After |
|---|---|---|
| Chipset | CC2652 (Texas Instruments) | EFR32MG24 (Silicon Labs) |
| Firmware | Z-Stack | Ember (EmberZNet) |
| Connection | USB (`/dev/serial/by-id/...`) | Ethernet PoE (`tcp://<STATIC_IP>:6638`) |
| Privileged pod | Yes (USB device passthrough) | No (network connection) |
| Device mounts | Required (`/dev/ttyACM0`) | Not required |

## Prerequisites

- SONOFF Dongle Max hardware
- Static IP address reserved on your network for the Dongle Max
- Access to the Zigbee2MQTT web interface
- Access to the environment values repository
- SSH access to the server hosting the Zigbee2MQTT data directory
- The `generic` Helm chart must accept `null` for `deviceMounts` and `securityContext`
  in its JSON schema (see "Generic Chart Requirements" at the end of this guide)

## Step 1: Back Up

1. Open your Zigbee2MQTT web interface
2. Navigate to **Tools**
3. Click **Request Backup** and download the backup zip file
4. Copy the backup zip to the server (e.g. to `/tmp/`):
   ```bash
   scp <backup-file>.zip <user>@<server>:/tmp/
   ```
5. Verify the zip contains `database.db` and `coordinator_backup.json`:
   ```bash
   unzip -l /tmp/<backup-file>.zip
   ```

Keep these backups until the migration is confirmed successful.

## Step 2: Record Your Current IEEE Address

The IEEE address is your Zigbee network's coordinator identity. Devices use it to
recognize the coordinator.

1. In Zigbee2MQTT, go to **Settings > About**
2. Find the **Coordinator** section
3. Copy the **IEEE Address** (format: `0x00124b00XXXXXXXX`)
4. Save this value -- you will need it in Steps 4 and 7

## Step 3: Set Up the SONOFF Dongle Max

1. Connect the Dongle Max to your network via Ethernet (PoE or with separate power)
2. Assign it a **static IP** in your router/DHCP server
3. Wait 30 seconds for it to initialize
4. Access the web console at `http://<STATIC_IP>`
5. Set an admin password (8+ characters) on first login
6. Verify the web console loads and shows device status

## Step 4: Write Your Old IEEE Address to the Dongle Max

This makes the new dongle appear as the same coordinator to your existing Zigbee devices.

**Via the Dongle Max web console (preferred):**

1. Navigate to **Z2M & ZHA**
2. Find **Zigbee Coordinator Migration** or **Advanced: Adapter IEEE Address Change**
3. Paste your IEEE address from Step 2
4. Click **Write IEEE Address**
5. Wait for confirmation (10-30 seconds)
6. Verify the web console now shows your old IEEE address as the current address

**Via command line (fallback):**

```bash
pip install universal-silabs-flasher
universal-silabs-flasher \
  --device tcp://<STATIC_IP>:6638 \
  write-ieee --ieee <IEEE_ADDRESS_WITHOUT_0x_PREFIX>
```

## Step 5: Stop Zigbee2MQTT and Disconnect Old Dongle

1. Disable zigbee2mqtt in your environment values file:
   ```yaml
   zigbee2mqtt:
     enabled: false
   ```
2. Commit and push. Wait for ArgoCD to sync and the pod to terminate.
3. Physically disconnect the old USB dongle from the server.

## Step 6: Prepare the Data Directory

SSH into the server and delete `coordinator_backup.json`. This file contains Z-Stack-specific
network state that is incompatible with Ember. If you skip this, Zigbee2MQTT will fail with
a "different adapter" error.

```bash
ssh <user>@<server>
cd <zigbee2mqtt-data-directory>
rm coordinator_backup.json
```

**Do NOT delete `database.db`** at this point. It will be overwritten in the next step
(which is expected), and restored from backup in Step 9.

## Step 7: Start Zigbee2MQTT with Ember/TCP Config (First Start)

> **IMPORTANT:** Before this step, verify that the Dongle Max still has your old IEEE
> address (from Step 4). The Ember initialization may reset the IEEE address on the dongle.
> If the address has changed, stop Zigbee2MQTT, re-write the IEEE address via the Dongle
> Max web console, delete the newly created `coordinator_backup.json` from the data
> directory, and repeat this step.
>
> The Ember coordinator state created during initialization is tied to the IEEE address.
> If it initializes with the wrong address, your devices will not recognize the coordinator.

Update your environment values to the new dongle configuration:

```yaml
zigbee2mqtt:
  enabled: true
  argocd:
    targetRevision: ~
    helm:
      values:
        devicePath: "tcp://<STATIC_IP>:6638"
        serialAdapter: ember
        generic:
          deployment:
            pvcMounts:
              data:
                hostPath: <your-data-directory-path>
```

The key changes compared to the Z-Stack/USB configuration:
- `devicePath` changes from the default `/dev/ttyACM0` to `tcp://<STATIC_IP>:6638`
- `serialAdapter` changes from `zstack` to `ember`
- `deviceMounts` is no longer specified (no USB device to mount)
- `securityContext` is no longer specified (no privileged access needed)

Commit and push. Wait for ArgoCD to sync and the pod to start.

**What happens during this first Ember start:**

The Ember coordinator initializes a new network since no Ember coordinator state exists
yet. During this process, Zigbee2MQTT **overwrites `database.db` with an empty database**
(~745 bytes), causing no devices to appear. This is expected and by design -- Z-Stack and
Ember use fundamentally different internal data structures, and the Ember adapter cannot
parse a Z-Stack database. It creates a fresh empty database as a safety mechanism.

**Why the start-stop-restore sequence (Steps 7-9) is required:**

The Ember coordinator must initialize first to:
1. Establish EZSP (EmberZNet Serial Protocol) communication with the hardware
2. Create its internal state tables
3. Initialize the network on the coordinator

Only after this initialization is complete can you safely restore the original device
database. The Ember coordinator initialization only happens on the **first start**. After
Step 7, the Ember coordinator state is persisted. When Zigbee2MQTT starts again in Step 10
(after restoring `database.db`), it finds the existing Ember coordinator state, skips
initialization, and reads the restored `database.db` with all device pairings intact.

You **cannot** skip the initial Ember start and restore `database.db` directly -- the Ember
adapter would either overwrite it again or fail with adapter errors.

Wait 1-2 minutes for the Ember coordinator to fully initialize, then proceed.

## Step 8: Verify IEEE Address After Ember Initialization

After the first Ember start, check that the coordinator is using the correct IEEE address.
You can verify this in the Zigbee2MQTT logs or web interface.

```bash
kubectl logs -n <namespace> -l app=zigbee2mqtt --tail=50
```

Look for your IEEE address (e.g. `0x00124b0024c8b8a5`) in the bridge info.

**If the IEEE address is wrong:**

The Ember initialization may have reset the dongle's IEEE address. If this happens:

1. Disable zigbee2mqtt in your environment values, commit, push, wait for pod to stop
2. Re-write the IEEE address to the Dongle Max via the web console (same as Step 4)
3. SSH to the server and delete the newly created `coordinator_backup.json`:
   ```bash
   ssh <user>@<server>
   cd <zigbee2mqtt-data-directory>
   rm coordinator_backup.json
   ```
4. Re-enable zigbee2mqtt, commit, push, wait for the pod to start
5. Verify the IEEE address is now correct before proceeding

This extra cycle is necessary because the Ember coordinator state is tied to the IEEE
address at the time of initialization. You must re-initialize with the correct address.

## Step 9: Stop Zigbee2MQTT and Restore Device Database

1. Disable zigbee2mqtt in your environment values:
   ```yaml
   zigbee2mqtt:
     enabled: false
   ```
2. Commit and push. Wait for ArgoCD to sync and the pod to terminate.

3. SSH into the server and restore `database.db` from the backup zip:
   ```bash
   ssh <user>@<server>
   cd <zigbee2mqtt-data-directory>
   python3 -c "
   import zipfile
   z = zipfile.ZipFile('/tmp/<backup-file>.zip')
   z.extract('database.db', '.')
   "
   chmod 777 database.db
   ```
   If `python3` is not available, copy the zip to a machine with `unzip`, extract
   `database.db`, and `scp` it back to the data directory.

4. Verify the restored file is the full-size database (not the empty Ember one):
   ```bash
   ls -la database.db
   ```
   The file should be significantly larger than 1 KB. The empty Ember database is
   roughly 745 bytes. Your original database with device pairings will be much larger.

## Step 10: Start Zigbee2MQTT Final

1. Re-enable zigbee2mqtt in your environment values:
   ```yaml
   zigbee2mqtt:
     enabled: true
   ```
2. Commit and push. Wait for ArgoCD to sync and the pod to start.

3. Verify the migration succeeded:
   - Open the Zigbee2MQTT web interface
   - All your devices should appear in the device list
   - Check the logs for successful Ember coordinator connection:
     ```bash
     kubectl logs -n <namespace> -l app=zigbee2mqtt --tail=50
     ```
   - Look for `EmberZNet` in the coordinator firmware version
   - Confirm the correct IEEE address in the bridge info

## Step 11: Network Stabilization (24 Hours)

After switching coordinators, the Zigbee mesh network needs time to rebuild routes:

- **First 30 minutes:** Most mains-powered devices (lights, plugs) should start responding.
  If battery-powered devices don't respond, remove and reinsert their batteries to force
  a rejoin.
- **24 hours:** Allow full mesh stabilization. Link quality and routing will settle during
  this period.
- **After 24 hours:** Test all critical automations and devices.

### Expectations for Z-Stack to Ember Migration

Because this is a cross-stack migration, not all devices may reconnect automatically:

- **70-80% of devices** will likely continue working without re-pairing (especially
  mains-powered routers)
- **20-30% of devices** may require manual re-pairing, especially battery-powered end
  devices with strict security settings

**Re-pairing strategy:**
1. Re-pair mains-powered routers closest to the coordinator first
2. Once routers are established, wake battery devices (remove/reinsert batteries)
3. Only re-pair devices that remain unresponsive after 30+ minutes

## Rollback Procedure

If the migration fails, you can fully revert to the old dongle:

1. Disable zigbee2mqtt in environment values, commit, push, wait for pod to stop
2. Reconnect the old USB dongle to the server
3. SSH to server and restore both files from backup:
   ```bash
   cd <zigbee2mqtt-data-directory>
   python3 -c "
   import zipfile
   z = zipfile.ZipFile('/tmp/<backup-file>.zip')
   z.extract('database.db', '.')
   z.extract('coordinator_backup.json', '.')
   "
   chmod 777 database.db coordinator_backup.json
   ```
4. Revert environment values to the original Z-Stack/USB configuration:
   ```yaml
   zigbee2mqtt:
     enabled: true
     argocd:
       targetRevision: ~
       helm:
         values:
           generic:
             deployment:
               pvcMounts:
                 data:
                   hostPath: <your-data-directory-path>
               deviceMounts:
                 zigbeeusb:
                   hostPath: <your-usb-device-path>
           serialAdapter: zstack
   ```
5. Commit, push, and let ArgoCD sync
6. Zigbee2MQTT will reconnect to the old dongle with the original network intact

## Troubleshooting

| Problem | Solution |
|---|---|
| Dongle Max web console not accessible | Verify it has power and network connectivity. Check your router for its IP. Try rebooting the dongle. |
| "Different adapter" error in logs | You forgot to delete `coordinator_backup.json` in Step 6. Stop Z2M, delete the file, restart. |
| No devices after Step 10 | `database.db` was not restored correctly. Check file size (should be >> 1 KB). Re-do Step 9. |
| Devices listed but not responding | Wait 30 minutes. Restart battery devices. Re-pair if still unresponsive after 30 min. |
| IEEE address wrong after Ember init | The Ember initialization reset the dongle's IEEE. Follow Step 8 to re-write and re-initialize. |
| IEEE address write failed | Ensure Zigbee2MQTT is stopped. Try the command-line flasher method. |
| Connection refused on port 6638 | Verify the Dongle Max is online and the static IP is correct. Check firewall rules. |
| Pod fails to start with schema error | Ensure the generic chart's JSON schema allows null for `deviceMounts` and `securityContext`. See below. |

## Generic Chart Requirements

The `apps/generic` chart must allow `null` values for `deviceMounts` and `securityContext`
in its JSON schema (`values.schema.json`). This is needed because the zigbee2mqtt base
`values.yaml` defines these with default values, and Helm's deep merge preserves them
even when the environment sets them to null.

Required schema changes in `apps/generic/values.schema.json`:

- `deployment.securityContext`: type must be `["object", "null"]`
- `deployment.deviceMounts`: type must be `["object", "null"]`
- `deployment.deviceMounts` additionalProperties: type must be `["object", "null"]`
- `deployment.deviceMounts` properties `hostPath` and `mountPath`: type must be
  `["string", "null"]`

The deployment template (`apps/generic/templates/deployment.yaml`) must also skip
deviceMount entries where `hostPath` is null, using `{{- if $mount.hostPath }}` guards
around both the volumeMount and volume definitions.

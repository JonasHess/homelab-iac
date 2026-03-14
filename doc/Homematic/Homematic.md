
# Homematic
Installation guide
https://github.com/jens-maus/RaspberryMatic/wiki/Installation-Kubernetes

If you get the following error during the installation:
```bash
g1905:~$ sudo modprobe eq3_char_loop
modprobe: ERROR: could not insert 'eq3_char_loop': Exec format error
```

Try the following (adapt the kernel version to your version):

```bash
uname -a
sudo apt reinstall linux-headers-$(uname -r)
sudo apt reinstall pivccu-modules-dkms
sudo service pivccu-dkms start
sudo modprobe eq3_char_loop
```

If you get the following error during the start of the container, or you don't have internet connetion:
```bash
Identifying Homematic RF-Hardware: ...cat: can't open '/sys/class/net/eth0/address': No such file or directory
```

Adapt the Mac-Address in the `10-network.rules` file to the correct one. You can find the correct Mac-Address with the following commands:
```bash
ip link show
```

```bash
sudo nano /etc/udev/rules.d/10-network.rules
```

add the following line to the file:
```bash
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="9c:6b:00:20:1e:41", NAME="eth0"
```

Reloead the configuration and reboot the system:
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo reboot
```

Maybe it needs some time till the application are reachable via the domains again.
I don't know why, but after some time it works again.

## Automatic fix after kernel update (fix_homematic.service)

A systemd oneshot service (`fix_homematic.service`) runs at boot to handle the `eq3_char_loop` module rebuild after kernel updates.

**Location:** `/usr/local/bin/fix_homematic.sh`

**How it works:**
1. First tries `modprobe eq3_char_loop` - if the module loads successfully (e.g., already loaded by `systemd-modules-load`), it exits early without changes
2. Only if the module fails to load, it rebuilds the DKMS modules by reinstalling `linux-headers` and `pivccu-modules-dkms`
3. After a successful rebuild, waits for microk8s to be ready, deletes the homematic pod (which destroys the eq3loop devices on termination), reloads the module to recreate devices, then lets k8s start a fresh pod

**Why the early exit check is critical:**
`systemd-modules-load` loads `eq3_char_loop` early at boot (configured in `/etc/modules-load.d/`). If the fix script unconditionally reinstalls the DKMS packages, the rebuilt module can have a kernel ABI mismatch (`.gnu.linkonce.this_module section size` error). The old working module stays in memory and masks the problem, but after the next pod restart the broken on-disk module fails to load. The early exit check prevents overwriting a working module.

**Install the script** on the server:
```bash
sudo tee /usr/local/bin/fix_homematic.sh << 'EOF'
#!/bin/bash

set -e

handle_error() {
    echo "Error in line $1"
    exit 1
}

trap 'handle_error $LINENO' ERR

# Try loading the module first - if it works, no fix needed
if sudo modprobe eq3_char_loop 2>/dev/null; then
    echo "eq3_char_loop loaded successfully, no fix needed."
    exit 0
fi

echo "eq3_char_loop failed to load, rebuilding DKMS modules..."

# Reinstall the kernel headers for the current kernel version
sudo apt reinstall -y linux-headers-$(uname -r)

# Reinstall the pivccu modules
sudo DEBIAN_FRONTEND=noninteractive apt reinstall -y pivccu-modules-dkms

# Start the pivccu dkms service
sudo service pivccu-dkms start

# Load the eq3_char_loop module
sudo modprobe eq3_char_loop

echo "Module rebuilt and loaded. Restarting homematic pod..."

# Wait for microk8s to be ready, then restart the homematic pod.
# The old pod's termination destroys the eq3loop devices (the container
# holds the master fd, closing it triggers device destruction and module
# unload). So we must: delete pod -> wait for termination -> reload module
# -> let k8s recreate the pod with fresh devices.
microk8s status --wait-ready --timeout 300
microk8s kubectl delete pod -n argocd -l app.kubernetes.io/name=openccu --wait 2>/dev/null || true
sleep 2
sudo modprobe eq3_char_loop

echo "Script execution completed successfully."
EOF
sudo chmod +x /usr/local/bin/fix_homematic.sh
```

**Install the systemd service:**
```bash
sudo tee /etc/systemd/system/fix_homematic.service << 'EOF'
[Unit]
Description=Automate pivccu fix after update
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/fix_homematic.sh

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable fix_homematic.service
```

**If homematic still doesn't start after a reboot**, check:
```bash
lsmod | grep eq3              # module loaded?
ls /dev/eq3loop /dev/mmd_*    # device nodes present?
journalctl -b | grep eq3      # any errors?
```
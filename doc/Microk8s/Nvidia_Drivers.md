# MicroK8s NVIDIA GPU Setup Guide

This guide documents how to enable GPU support in MicroK8s using host-installed NVIDIA drivers.

## Prerequisites

- MicroK8s 1.22 or newer installed
- NVIDIA GPU present in the system
- NVIDIA kernel modules loaded on the host

## Check Current State

### Verify NVIDIA kernel modules are loaded

```bash
lsmod | grep nvidia
```

Expected output shows `nvidia`, `nvidia_modeset`, `nvidia_drm` modules.

### Check kernel driver version

```bash
cat /proc/driver/nvidia/version
```

## Install Host Driver Userspace Tools

If you have NVIDIA kernel modules but `nvidia-smi` is not available, install the utils package matching your driver version:

```bash
# Check which driver version is installed
dpkg -l | grep -i nvidia | grep modules

# Install matching utils package (example for 535-server)
sudo apt install -y nvidia-utils-535-server
```

Verify installation:

```bash
nvidia-smi
```

## Enable GPU Addon

### Using Host Drivers (Recommended when drivers are pre-installed)

```bash
microk8s enable gpu --driver host
```

This tells the GPU operator to use the existing host drivers instead of trying to install its own.

### Alternative: Auto-detect (default)

```bash
microk8s enable gpu
```

The `--driver auto` mode (default) will attempt to use host drivers if found, otherwise install drivers via the operator.

## Verify Installation

### Check all pods are running

```bash
microk8s kubectl get pods -n gpu-operator-resources
```

All pods should be `Running` or `Completed`.

### Check validator logs

```bash
microk8s kubectl logs -n gpu-operator-resources -lapp=nvidia-operator-validator -c nvidia-operator-validator
```

Expected output:
```
all validations are successful
```

## Troubleshooting

### Problem: nvidia-driver-daemonset in CrashLoopBackOff

**Cause:** The operator is trying to install its own drivers but cannot unload the existing host kernel modules.

**Solution:** Re-enable the addon with `--driver host`:

```bash
microk8s disable gpu
microk8s enable gpu --driver host
```

### Problem: Pods stuck in Init state with "failed to validate the driver"

**Cause:** The driver validation cannot find `nvidia-smi` or other userspace tools.

**Solution:** Install the NVIDIA utils package:

```bash
sudo apt install -y nvidia-utils-535-server  # Match your driver version
```

Then restart the stuck pods:

```bash
microk8s kubectl delete pods -n gpu-operator-resources -l app=nvidia-container-toolkit-daemonset
```

### Problem: "resource temporarily unavailable" when unloading modules

**Cause:** NVIDIA kernel modules are in use (typically by a display server).

**Solution:** Either:
1. Use `--driver host` to skip driver installation
2. Stop the display manager before enabling the addon
3. Reboot into multi-user target (no GUI)

## Test GPU Workload

Deploy a test pod:

```bash
microk8s kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: cuda-vector-add
spec:
  restartPolicy: OnFailure
  containers:
    - name: cuda-vector-add
      image: "k8s.gcr.io/cuda-vector-add:v0.1"
      resources:
        limits:
          nvidia.com/gpu: 1
EOF
```

Check the result:

```bash
microk8s kubectl logs cuda-vector-add
```

Expected output:
```
[Vector addition of 50000 elements]
Copy input data from the host memory to the CUDA device
CUDA kernel launch with 196 blocks of 256 threads
Copy output data from the CUDA device to the host memory
Test PASSED
Done
```

## Environment Reference

This guide was tested with:

| Component | Version |
|-----------|---------|
| Kernel | 6.8.0-88-generic |
| NVIDIA Driver | 535.274.02 |
| CUDA | 12.2 |
| GPU Operator | v25.10.0 |
| GPU | Quadro P400 |

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
scp homeserver@192.168.0.20:/home/homeserver/.kube/config ~/.kube/config.d/config
chmod 600 ~/.kube/config.d/config
```

# Managing MicroK8s Containers
## List Container Images

To list all container images in MicroK8s:

```bash
microk8s ctr images ls | grep <my_image>:<tag>
```
This command filters the list to show only the image <my_image>:<tag>.

## Remove a Container Image

```bash
microk8s ctr images rm <my_image>:<tag>
```

This command deletes the image from your MicroK8s environment.

## Restarting Kubernetes Deployments

To restart a deployment in your Kubernetes cluster:

```bash
kubectl rollout restart deployment <my_deployment> -n <my_namespace>
```

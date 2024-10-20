
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

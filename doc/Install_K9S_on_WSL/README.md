
## Install k9s on WSL
https://gist.github.com/bplasmeijer/a4845a4858f1c0b0a22848984475322d

curl -s -L https://github.com/derailed/k9s/releases/download/v0.32.5/k9s_Linux_amd64.tar.gz -o k9s && tar -xvf k9s && chmod 755 k9s && rm LICENSE README.md  && sudo mv k9s /usr/local/bin

## Refresh certificates (e.g. after IP change)
```bash
sudo microk8s refresh-certs --cert ca.crt
```
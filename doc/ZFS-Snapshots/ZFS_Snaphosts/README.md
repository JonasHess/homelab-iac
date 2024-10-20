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

We replaced this in the script  to fix a 404 error, if the ubuntu release is not found:
```text
CODENAME="ubuntu noble"
```



```bash
apt-get install zrepl
```


## ZRepl config
copy the ```zrepl.yaml``` configuration to /etc/zrepl/zrepl.yml

```bash
ssh root@192.168.1.3 "mkdir -p /etc/zrepl"
scp ./zrepl.yml root@192.168.1.3:/etc/zrepl/zrepl.yml
```



```bash
zrepl configcheck
```

```bash
service zrepl restart
# or 
systemctl restart zrepl
```


```bash
zrepl status
```
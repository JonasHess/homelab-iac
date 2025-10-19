# SSH - Disable clear text passwords & Enable root login

## Copy the public key to the server
```bash
ssh-copy-id myuser@<server_ip>
```

or

curl -fsSL https://raw.githubusercontent.com/JonasHess/SSH_Public_Key/master/setup.sh -o installKeys.sh && sh installKeys.sh

## Login to the server
```bash
ssh myuser@<server_ip>
```  


## Switch to root user
```bash
sudo -i
```


## Open the sshd_config file
```bash
vim /etc/ssh/sshd_config
```

## Set the following values:
```text


PermitRootLogin prohibit-password
PasswordAuthentication no
```


## Restart the ssh service
```bash
service ssh restart
```

cat /home/myuser/.ssh/authorized_keys >> /root/.ssh/authorized_keys
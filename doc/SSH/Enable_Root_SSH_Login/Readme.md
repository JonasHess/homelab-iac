# Change to no to disable tunnelled clear text passwords

## Open the sshd_config file
```bash
vim /etc/ssh/sshd_config
```

## Set the following line to no
```text


PermitRootLogin prohibit-password

# Change to no to disable tunnelled clear text passwords
PasswordAuthentication no
```


## Restart the ssh service
```bash
service ssh restart
```

cat authorized_keys >> /root/.ssh/authorized_keys
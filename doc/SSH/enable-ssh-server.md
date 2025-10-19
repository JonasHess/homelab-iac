# Steps to Enable SSH on Ubuntu Server

## 1. Update your package lists

Open a terminal or connect via console and run:

```bash
sudo apt update && sudo apt upgrade -y
```

This ensures the latest package versions are installed.

## 2. Install the OpenSSH server

Use the following command to install the SSH server package:

```bash
sudo apt install openssh-server -y
```

This installs the OpenSSH service that handles SSH connections.

## 3. Enable and start the SSH service

Run this command to start SSH immediately and ensure it runs at boot:

```bash
sudo systemctl enable --now ssh
```

You can verify that the service is running with:

```bash
sudo systemctl status ssh
```

Look for a line containing **"Active: active (running)"**.

## 4. Allow SSH through the firewall (UFW)

If Ubuntu's firewall (ufw) is active, you must open the SSH port (22):

```bash
sudo ufw allow ssh
sudo ufw reload
```

Confirm the rule with:

```bash
sudo ufw status
```

You should see a line like `22/tcp ALLOW`.

## 5. Connect to your Ubuntu server remotely

From another machine, connect using:

```bash
ssh username@server_ip
```
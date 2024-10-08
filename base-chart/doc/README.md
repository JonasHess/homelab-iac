# Setup OIDC Traefik-Forward-Auth with AWS Cognito

This guide will walk you through the process of setting up OIDC Traefik-Forward-Auth with AWS Cognito.

## Prerequisites

- AWS account with access to Cognito service.
- Domain names configured: `auth.your-domain.com`


## Steps

1. **Create a Cognito User Pool**

   Navigate to the AWS Cognito service and create a new User Pool. Take note of the User Pool ID, it will be used later.

2. **Create a Cognito App Client**

   Within the User Pool, create a new App Client. Make sure to AppType as  `Trusted Client`.
   Check the OAuth scopes and make sure to enable "Profile".

   Set the callback URL to `https://auth.your-domain.com/_oauth`. Take note of the App Client ID and Client Secret, it will be used later.

3. **Create a user in the User Pool**

   Create a new user in the User Pool.

4. **Configure OIDC in `values.yaml`**

   Update the `values.yaml` file with the following values:

   ```yaml
   traefik_forward_auth:
     oidc_issuer_url: "https://cognito-idp.<aws-region>.amazonaws.com/<user-pool-id>/.well-known/jwks.json"
     oidc_client_id: "<app-client-id>"
   ```

   Replace `<aws-region>` with your AWS region, `<user-pool-id>` with your User Pool ID, and `<app-client-id>` with your App Client ID.


5. **Set Client Secret**

   Create a Secret in Akeyless with the Client Secret value under the key `{{.Values.apps.akeyless.path}}/oidc/traefik-forward-auth/client_secret`

![FireShot Capture 017 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20017%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 018 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20018%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 019 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20019%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 020 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20020%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 021 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20021%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)

# Homeassistant
Add the following to your `configuration.yaml` file. IP 10.1.0.0/16 is the IP-range of the kubernetes cluster.
```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - 10.1.0.0/16
    - ::1
```

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
sudo apt reinstall linux-headers-5.13.0-35-generic
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
# Setup OIDC with AWS Cognito

This guide walks through setting up OIDC against AWS Cognito for use with
Envoy Gateway's native `SecurityPolicy.oidc` filter. See
[`docs/gateway-and-oidc.md`](../../docs/gateway-and-oidc.md) for the full
architecture; this file covers only the Cognito- and Akeyless-side steps.

## Prerequisites

- AWS account with access to Cognito.
- Domain set up via cert-manager + Envoy Gateway (see main docs).
- The auth-callback subdomain `auth.<your-domain>` resolves to the gateway.

## Steps

1. **Create a Cognito User Pool**

   In AWS Cognito, create a new User Pool. Note the **User Pool ID** and
   the **region** — you'll need them for `global.oidc.issuerUrl`.

2. **Create a Cognito App Client**

   Inside the User Pool, create an App Client (AppType: `Trusted Client`).
   Enable the OAuth scopes you need — at minimum `openid`, `email`, and
   `profile`.

   Set the **Allowed callback URL** to:

   ```
   https://auth.<your-domain>/oauth2/callback
   ```

   Note the **App Client ID** and **App Client Secret**.

3. **Create a user**

   Create a user in the User Pool (with the email-verified flag, or do a
   first-login forced password change).

4. **Configure the env file**

   In `homelab_environments/<env>/values.yaml`, set the three
   `global.oidc.*` fields:

   ```yaml
   global:
     oidc:
       issuerUrl: "https://cognito-idp.<aws-region>.amazonaws.com/<user-pool-id>"
       clientId:  "<app-client-id>"
       cookieDomain: "<your-domain>"   # NO leading dot — Envoy rejects it
   ```

   `clientId` is intentionally plaintext — it's not a secret per the OIDC
   spec (it appears in every auth-redirect URL).

5. **Store the client secret in Akeyless**

   Create a Static Secret in Akeyless at:

   ```
   <global.akeyless.path>/oidc/client_secret
   ```

   (For `zimmermann.lat` that's `/zimmermann.lat/oidc/client_secret`.)

   The Akeyless path itself is configurable via
   `global.oidc.clientSecretAkeylessPath` in `base-chart/values.yaml`.
   Three ExternalSecrets in iac (envoy-gateway, argocd, sftpgo) pull
   from this single path.

![FireShot Capture 017 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20017%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 018 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20018%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 019 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20019%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 020 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20020%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)
![FireShot Capture 021 - Benutzerpool erstellen } Benutzerpools } Amazon Cognito } eu-central-_ - eu-central-1.console.aws.amazon.com.png](img%2FFireShot%20Capture%20021%20-%20Benutzerpool%20erstellen%20%7D%20Benutzerpools%20%7D%20Amazon%20Cognito%20%7D%20eu-central-_%20-%20eu-central-1.console.aws.amazon.com.png)

# Home Assistant — trusted_proxies

Home Assistant must trust the gateway as a proxy so it sees the real
client IP (forwarded as `X-Forwarded-For`). Add to `configuration.yaml`:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - 10.1.0.0/16        # adjust to your cluster pod CIDR
    - ::1
```

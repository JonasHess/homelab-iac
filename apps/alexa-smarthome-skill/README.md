# Alexa Smart Home Skill

Connects Alexa to Home Assistant using the Smart Home API (v3). This enables native voice control like *"Alexa, schalte das Licht an"* — no custom invocation phrase needed.

## How it works

On every ArgoCD sync, a **PostSync Job** (`deploy.py`) automatically:

1. Refreshes an LWA access token
2. Updates the skill manifest via SMAPI
3. Updates account linking configuration via SMAPI
4. Zips and uploads the Lambda function code to AWS

The Lambda function (`lambda_function.py`) receives Alexa Smart Home directives and forwards them to Home Assistant's `/api/alexa/smart_home` endpoint.

### Shared secrets

This app reuses the same Akeyless secrets as `alexa-custom-skill` (LWA credentials + AWS keys). Both skills share the path `/alexa-custom-skill/` in Akeyless.

## One-time setup

### 1. Create the Lambda function

1. Go to https://console.aws.amazon.com/lambda/ and select **eu-west-1** (Ireland)
2. Click **Create function** > **Author from scratch**
3. Set **Name** to `HomeAssistant` and **Runtime** to `Python 3.12`
4. Click **Create function**
5. Go to **Configuration** > **Environment variables** > **Edit**
6. Add `BASE_URL` = `https://<your-home-assistant-url>`
7. Copy the **Function ARN** from the top of the page (you'll need it later)

### 2. Create the Alexa Skill

1. Go to https://developer.amazon.com/alexa/console/ask
2. Click **Create Skill**
3. Set **Name** (e.g. `HomeAssistant`), **Locale** to `German (DE)`, **Type** to **Smart Home**
4. Click **Create Skill**
5. Copy the **Skill ID** from the console URL or overview page (`amzn1.ask.skill.xxxxx`)

Now link the Lambda to the skill — **this must be done before setting the endpoint**:

6. Go back to the **AWS Lambda console** > your `HomeAssistant` function
7. Click **Add trigger** > select **Alexa Smart Home**
8. Paste the **Skill ID** from step 5 > click **Add**

Now set the endpoint in the Alexa console:

9. Go back to the **Alexa Developer Console** > your skill
10. Paste the **Lambda ARN** into the **Default endpoint** field
11. Click **Save**

### 3. Create a Login with Amazon (LWA) Security Profile

> If you already have an LWA profile from the Custom Skill, you can reuse it — skip to step 4.

1. Go to https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html
2. Click **Create a New Security Profile**
3. Fill in:
   - **Name**: e.g. `Skill Deployment`
   - **Description**: e.g. `Used to deploy skills via SMAPI`
   - **Consent Privacy Notice URL**: `https://example.com` (required but not actually used)
4. Click **Save**
5. Click the **gear icon** next to your profile > **Web Settings** > **Edit**
6. Under **Allowed Return URLs**, add:
   - `http://127.0.0.1:9090/cb`
   - `https://ask-cli-static-content.s3-us-west-2.amazonaws.com/html/ask-cli-no-browser.html`
7. Click **Save**
8. Note the **Client ID** and **Client Secret**

### 4. Generate LWA refresh token

Install the ASK CLI if you haven't already:

```bash
npm install -g ask-cli
ask configure
```

When `ask configure` opens a browser, sign in with your Amazon Developer account. When asked to link your AWS account, select **No**.

Then generate the token:

```bash
ask util generate-lwa-tokens \
  --client-id <CLIENT_ID_FROM_STEP_3> \
  --client-confirmation <CLIENT_SECRET_FROM_STEP_3> \
  --scopes "alexa::ask:skills:readwrite" \
  --no-browser
```

1. Open the printed URL in your browser
2. Log in and grant access
3. Copy the `refresh_token` from the terminal output

### 5. Create an IAM user for Lambda deployment

> If you already have an IAM user from the Custom Skill, you can reuse it — just make sure its policy allows `lambda:UpdateFunctionCode` on this Lambda ARN.

1. Go to https://console.aws.amazon.com/iam/ > **Users** > **Create user**
2. Name: `alexa-skill-deployer` > **Next** > skip permissions > **Create user**
3. Open the user > **Permissions** tab > **Add permissions** > **Create inline policy**
4. Switch to **JSON** and paste:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["lambda:UpdateFunctionCode"],
       "Resource": "<your-lambda-arn>"
     }]
   }
   ```
5. Name it `AlexaSmartHomeLambdaDeploy` > **Create policy**
6. Go to **Security credentials** tab > **Create access key**
7. Select **Application running outside AWS** > add a description (e.g. `ArgoCD PostSync job to deploy Alexa Smart Home skill Lambda code`) > **Create access key**
8. Copy the **Access Key ID** and **Secret Access Key** (shown only once)

### 6. Store secrets in Akeyless

Store these under `<akeyless-path>/alexa-custom-skill/` (shared with the Custom Skill):

| Secret | Source |
|--------|--------|
| `LWA_CLIENT_ID` | Step 3 |
| `LWA_CLIENT_SECRET` | Step 3 |
| `LWA_REFRESH_TOKEN` | Step 4 |
| `AWS_ACCESS_KEY_ID` | Step 5 |
| `AWS_SECRET_ACCESS_KEY` | Step 5 |

### 7. Configure the environment values

Add the following to your environment values file (e.g. `homelab-environments/<env>/values.yaml`):

```yaml
alexa-smarthome-skill:
  enabled: true
  argocd:
    namespace: "homeassistant"
    targetRevision: ~
    helm:
      values:
        skillId: "<skill-id-from-step-2>"
        lambdaArn: "<lambda-arn-from-step-1>"
        lambdaRegion: "eu-west-1"
        homeAssistantUrl: "https://<your-home-assistant-url>"
        files:
          skillJson: |
            {
              "manifest": {
                "apis": {
                  "smartHome": {
                    "endpoint": {
                      "uri": "{{ .Values.lambdaArn }}"
                    },
                    "protocolVersion": "3"
                  }
                },
                "manifestVersion": "1.0",
                "publishingInformation": {
                  "locales": {
                    "de-DE": {
                      "name": "HomeAssistant"
                    }
                  }
                }
              }
            }
```

Push both repos. ArgoCD will deploy the app and the PostSync job will configure the skill automatically.

### 8. Enable the skill and link your account

1. Open the **Alexa app** on your phone
2. Go to **Mehr** > **Skills & Spiele** > **Deine Skills** > **Entwickler**
3. Tap your skill (e.g. "HomeAssistant") > **Aktivieren**
4. Log in to Home Assistant when prompted to link your account

### 9. Discover devices

After account linking, tell Alexa to find your devices:

- Say *"Alexa, suche nach neuen Geräten"*
- Or in the Alexa app: **Geräte** > **+** > **Gerät hinzufügen** > **Sonstiges** > **Geräte suchen**

After this one-time setup, all future changes are deployed automatically via ArgoCD.

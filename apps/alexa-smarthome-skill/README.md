# Alexa Smart Home Skill

Deploys an Alexa Smart Home Skill via ArgoCD. On every sync, a PostSync Job automatically:

1. Updates the skill manifest via SMAPI
2. Updates account linking configuration via SMAPI
3. Zips and uploads the Lambda function code via boto3

Unlike the Custom Skill, there is no interaction model update — Smart Home skills use built-in Alexa directives.

## One-time setup

### 1. Create the Lambda function in AWS Console

> Skip this step if you already have a Lambda function for Home Assistant Smart Home (e.g. `HomeAssistant`).

- Go to https://console.aws.amazon.com/lambda/ (select the `eu-west-1` region)
- Create function > Author from scratch
- Name: `HomeAssistant`, Runtime: Python 3.12
- Add environment variable: `BASE_URL` = `https://<your-ha-url>`
- Copy the Function ARN → use as `lambdaArn`

### 2. Create the Alexa Skill

#### Option A: Via Alexa Developer Console (recommended)

1. Go to https://developer.amazon.com/alexa/console/ask
2. Click **"Create Skill"**
3. Set **Name** (e.g. `HomeAssistant`), **Locale** to `German (DE)`, and **Type** to **Smart Home**
4. Click **"Create Skill"**

> **Important**: Before setting the endpoint in the Alexa console, you must first add the **Alexa Smart Home** trigger to the Lambda function. Otherwise saving the endpoint will fail.

5. Go to the Lambda function in the AWS Console
6. Click **"Add trigger"** → select **"Alexa Smart Home"** → paste the **skill ID** (visible in the Alexa console URL or overview page) → click **"Add"**
7. Back in the Alexa Developer Console, paste the Lambda ARN into the **Default endpoint** field and click **"Save"**

#### Option B: Via ASK CLI

- Install ASK CLI:
  ```bash
  npm install -g ask-cli
  ask configure
  ```
  - It will open a browser to sign in with your Amazon Developer account
  - When asked "Do you want to link your AWS account?", select **No**

- Create a Smart Home skill:
  ```bash
  ask smapi create-skill-for-vendor \
    --manifest "$(cat <<'JSON'
      {"manifest":{"manifestVersion":"1.0","publishingInformation":{"locales":{"de-DE":{"name":"HomeAssistant"}}},"apis":{"smartHome":{"endpoint":{"uri":"<lambdaArn>"},"protocolVersion":"3"}}}}
  JSON
  )"
  ```
- Copy the returned `skillId`
- Add **Alexa Smart Home** trigger to the Lambda function with the `skillId`

### 3. Create a Login with Amazon (LWA) Security Profile

> You can reuse the same LWA Security Profile from the Custom Skill if you already have one. Skip to step 4 if so.

- Open https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html
- Click **"Create a New Security Profile"**
- Fill in the form:
  - Security Profile Name: e.g. `Skill Deployment`
  - Security Profile Description: e.g. `Used to deploy skills via SMAPI`
  - Consent Privacy Notice URL: e.g. `https://example.com` (required but not used)
- Click **"Save"**
- Click the gear icon next to your new Security Profile
- Click the **"Web Settings"** tab
- Click **"Edit"**
- Under **"Allowed Return URLs"**, add both:
  - `http://127.0.0.1:9090/cb`
  - `https://ask-cli-static-content.s3-us-west-2.amazonaws.com/html/ask-cli-no-browser.html`
- Click **"Save"**
- Note the **Client ID** and **Client Secret** (click "Show Secret" to reveal it)

### 4. Get LWA refresh token

This generates a long-lived refresh token that the deploy Job uses to authenticate with the Alexa SMAPI API.

> Smart Home skills don't need the `alexa::ask:models:readwrite` scope since there is no interaction model.

- Make sure ASK CLI is installed (from step 2)
- Run the following command, replacing the two placeholders with the Client ID and Client Secret from step 3:
  ```bash
  ask util generate-lwa-tokens \
    --client-id <LWA_CLIENT_ID> \
    --client-confirmation <LWA_CLIENT_SECRET> \
    --scopes "alexa::ask:skills:readwrite" \
    --no-browser
  ```
- It will print a URL - open it in your browser
- Log in with your Amazon Developer account and grant access
- It will redirect to localhost and print the tokens in the terminal
- Copy the `refresh_token` value → this is your `LWA_REFRESH_TOKEN`

### 5. Create an IAM user with Lambda update permissions

> You can reuse the same IAM user from the Custom Skill if the policy allows access to this Lambda function. If reusing, just ensure the policy `Resource` includes this Lambda ARN.

- Open https://console.aws.amazon.com/iam/
- In the left sidebar, click **"Users"**
- Click **"Create user"**
- Enter a name (e.g. `alexa-skill-deployer`)
- Leave "Provide user access to the AWS Management Console" unchecked
- Click **"Next"**
- On the permissions page, don't add anything, click **"Next"**
- Click **"Create user"**
- Click on the newly created user name to open it
- Click the **"Permissions"** tab
- Click **"Add permissions"** → **"Create inline policy"**
- Click the **"JSON"** tab and paste:
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["lambda:UpdateFunctionCode"],
      "Resource": "<lambdaArn>"
    }]
  }
  ```
- Click **"Next"**, enter a policy name (e.g. `AlexaSmartHomeLambdaDeploy`)
- Click **"Create policy"**
- Click the **"Security credentials"** tab
- Scroll down to "Access keys" and click **"Create access key"**
- Select **"Application running outside AWS"**, click **"Next"**
- Click **"Create access key"**
- Copy the **Access Key ID** and **Secret Access Key** (shown only once!)

### 6. Store secrets in Akeyless

Store these under `<akeyless-path>/alexa-smarthome-skill/`:

| Secret | Source |
|--------|--------|
| `LWA_CLIENT_ID` | Step 3 |
| `LWA_CLIENT_SECRET` | Step 3 |
| `LWA_REFRESH_TOKEN` | Step 4 |
| `AWS_ACCESS_KEY_ID` | Step 5 |
| `AWS_SECRET_ACCESS_KEY` | Step 5 |

### 7. Enable the skill and link your account

- Open the Alexa app on your phone
- Go to **"Mehr"** → **"Skills & Spiele"**
- Tap **"Deine Skills"** → **"Entwickler"**
- You should see your skill (e.g. "HomeAssistant2") → tap it
- Tap **"Aktivieren"**
- You will be redirected to your Home Assistant login page
- Log in to link your account
- Once linked, Alexa can discover and control your Smart Home devices: *"Alexa, schalte das Licht an"*

### 8. Discover devices

After account linking, tell Alexa to discover your devices:

- Say *"Alexa, suche nach neuen Geräten"*
- Or in the Alexa app: **"Geräte"** → **"+"** → **"Gerät hinzufügen"** → **"Sonstiges"** → **"Geräte suchen"**

After this one-time setup, all future changes are automated via ArgoCD.

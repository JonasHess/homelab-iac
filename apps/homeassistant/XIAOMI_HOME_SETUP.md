# Xiaomi Home Integration Setup

## Prerequisites
- Home Assistant running in Kubernetes cluster
- HACS (Home Assistant Community Store) installed

## Installation Steps

### 1. Install Integration via HACS
- Open HACS in Home Assistant
- Go to Integrations
- Search for "Xiaomi Home"
- Install the integration
- Restart Home Assistant

### 2. Port Forward Home Assistant Service
```bash
kubectl port-forward svc/homeassistant-service 8123:8123 -n services
```

### 3. Configure /etc/hosts
Add this entry to your `/etc/hosts` file:
```
127.0.0.1 homeassistant.local
```

### 4. Access Home Assistant
- Open browser to `http://homeassistant.local:8123`

### 5. Add Xiaomi Home Integration
- Go to Settings → Devices & Services
- Click "Add Integration"
- Search for "Xiaomi Home"
- Click to configure

### 6. OAuth2 Login Configuration
- Select **Europe servers** when prompted
- Use your **account ID** (not email address) for login
- Enter your password
- Complete OAuth2 authentication flow

## Password Reset
If you need to reset your Xiaomi account password:
- Visit: https://account.xiaomi.com/fe/service

## Notes
- The integration requires OAuth2 authentication with Xiaomi servers
- Europe server selection is recommended for European users
- Account ID is different from email address - check your Xiaomi account settings
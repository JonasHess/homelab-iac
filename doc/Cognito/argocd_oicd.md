# ArgoCD Cognito SSO OIDC

## Overview

This document describes how to configure ArgoCD to use Cognito as an OIDC provider for SSO.

## Cognito Configuration

1. Create a Group in Cognito called `argocd-admin`.
2. Add a user to the group.
3. Create an App Client in Cognito.
4. Configure the App Client with the following settings:
   - Set the `Callback URL`to the URL of your ArgoCD instance with `/auth/callback` appended.
      Callback URL: `https://argocd.example.com/auth/callback`
5. If not working, restart the argo-cd server pod!

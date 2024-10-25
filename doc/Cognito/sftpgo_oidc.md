# SFTPGO OIDC with Cognito

# Add a custom attribute to the Cognito user pool

- Go to ```Amazon Cognito > User pools> nudelkiste> Add custom attributes```
- Add a custom attribute called ```sftpgo_role```
- Give your users the custom attribute ```custom:sftpgo_role``` with the value ```admin```


# Add read permission to the custom attribute
- Go to > Amazon Cognito> User pools> nudelkiste> App client: home-server-dev traefik-forward-auth> Edit attribute read and write permissi
- Give Read permission to attribute: ```custom:sftpgo_role``` 

# Add redirect URL to the Cognito App Client

- Go to ```Amazon Cognito > User pools> nudelkiste> App clients```
- Add a new callback URL with the value ```https://sftpgo.example.com/web/oidc/redirect```

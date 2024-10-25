# SFTPGO OIDC with Cognito

# Add a custom attribute to the Cognito user pool

- Go to ```Amazon Cognito > User pools> nudelkiste> Add custom attributes```
- Add a custom attribute called ```sftpgo_role```
- Add a custom attribute called ```sftpgo_username```
- Give your users the custom attribute ```custom:sftpgo_role``` with the value ```admin``` (the role of the user in SFTP Go or 'admin' for admin users)
- Give your users the custom attribute ```custom:sftpgo_username``` with the value ```my_sftpGo_user``` (the username of the user in SFTP Go)
- Ensure the user and the role is created in SFTP Go (The role 'admin' is a default role in SFTP Go)

# Add read permission to the custom attribute
- Go to > Amazon Cognito> User pools> nudelkiste> App client: home-server-dev traefik-forward-auth> Edit attribute read and write permissi
- Give Read permission to attribute: ```custom:sftpgo_role``` 
- Give Read permission to attribute: ```custom:sftpgo_username```

# Add redirect URL to the Cognito App Client

- Go to ```Amazon Cognito > User pools> nudelkiste> App clients```
- Add a new callback URL with the value ```https://sftpgo.example.com/web/oidc/redirect```

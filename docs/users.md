# Users

Add users:

```bash
sftpwarden user add alice -c dev
sftpwarden user add bob --password "correct horse battery staple" -c dev
sftpwarden user add carol --password-hash '$6$...' -c dev
```

Manage users:

```bash
sftpwarden users -c dev
sftpwarden user show alice -c dev
sftpwarden user update alice --public-key "ssh-ed25519 AAAA..." -c dev
sftpwarden user remove alice -c dev --yes
```

Removing a user from the provider does not delete user data.


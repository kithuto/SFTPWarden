# Getting Started

```bash
sftpwarden config default-provider yaml
sftpwarden init dev --root ~/sftpwarden-dev --yes
cd ~/sftpwarden-dev
sftpwarden validate
sftpwarden compose --write
docker compose up -d --build
```

Add a user:

```bash
sftpwarden user add alice -c dev
```

If no password or hash is passed, the CLI prompts for a password and stores only a hash.


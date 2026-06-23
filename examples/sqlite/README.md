# SQLite Example

SQLite is built in and works well for a small single-host deployment.

```bash
mkdir -p ~/sftpwarden-sqlite
cd ~/sftpwarden-sqlite
sftpwarden init dev --provider sqlite --yes
sftpwarden deploy
```

The provider file is `users.sqlite`. Avoid SQLite for NFS, high concurrency, or
multi-writer deployments.

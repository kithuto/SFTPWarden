# Remote Local-Sync Example

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical
```

Local project files are the source of truth. The watcher syncs editable
YAML/CSV/SQLite provider files to the remote host; it does not sync
`sftpwarden.yaml`. Use `sftpwarden refresh` after provider changes when you want
the runtime to apply them immediately, and use `sftpwarden deploy` for config or
Compose changes.

Remote local-sync contexts install a watcher automatically. The default watcher
mode is `auto`, which detects the host scheduler. Force a backend only when your
environment requires it:

```bash
sftpwarden watcher install --watcher systemd
sftpwarden watcher install --watcher docker --yes
```

Use `sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical`
only when that remote project already exists and you want to register it locally.

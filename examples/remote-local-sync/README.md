# Remote Local-Sync Example

This example shows a remote context where local project files stay on your
machine and SFTPWarden deploys them to a Docker host over SSH.

Create a new remote local-sync context:

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical \
  --yes
sftpwarden deploy --dry-run
```

Local project files are the source of truth. The watcher syncs editable
YAML/CSV/SQLite provider files to the remote host; it does not sync
`sftpwarden.yaml`. Use `sftpwarden refresh` after provider changes when you want
the runtime to apply them immediately, and use `sftpwarden deploy` for config or
deployment changes.

Remote local-sync contexts install a watcher automatically. The default watcher
mode is `auto`, which detects the host scheduler. Force a backend only when your
environment requires it:

```bash
sftpwarden watcher install --watcher systemd
sftpwarden watcher install --watcher docker --yes
```

Use `context add` only when that remote project already exists and you want to
register it locally:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden \
  --critical \
  --yes
sftpwarden deploy --context prod --dry-run
```

# Remote Local-Sync Example

Keep project files on your computer and run SFTP on a remote Docker host.
SFTPWarden deploys over SSH and the watcher delivers later provider-file edits.

You need SSH access as a deployment administrator, Docker Compose v2 on the
remote host and a writable project destination. Replace the example account and
hostname. The deployment administrator is separate from the SFTP user Alice.

## Create and deploy

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden --critical
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
sftpwarden health
sftpwarden watcher status
```

Choose Alice's password when prompted. Initialization can install the watcher;
review the detected backend before accepting it. The remote server must be able
to pull the configured runtime image.

## Verify delivery

Connect to the remote host's SFTP address and configured published port, then
upload and download a file. Check the server fingerprint through your trusted
administrative connection before accepting it.

After a user edit, verify a fresh SFTP login. The watcher syncs editable
YAML/CSV/SQLite provider files and applies runtime-relevant changes. It does not
deliver `sftpwarden.yaml`, `.env` or uploaded files. Configuration changes
require another deploy.

Native watcher backends use your host SSH configuration and agent. See
[Operations](../../docs/operations.md#watcher) for backend selection and
[Docker watcher](../watcher-docker/README.md) for explicit-key requirements.

For an already-existing installation, use `sftpwarden context add` and inspect
the registered local root and remote endpoint before deploying.

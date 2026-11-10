# Docker Watcher Example

Use the Docker watcher to deliver provider-file changes from your computer to
remote local-sync projects when a native scheduler is unsuitable.
It does not run the SFTP server or apply project configuration.

You need Docker, an existing remote local-sync project and a dedicated deployment
SSH key authorized on the remote host. Native backends are preferable when you
depend on your usual SSH agent, config or bastion rules.

## Register the deployment key

For a new local-sync project, work in a fresh directory and replace the host and
key path with your own:

```bash
sftpwarden init prod \
  --remote deploy@example.com:/opt/sftpwarden \
  --ssh-key ~/.ssh/sftpwarden_deploy \
  --watcher docker \
  --critical
```

Initialization can install the watcher. Confirm the status before installing it
again:

```bash
sftpwarden watcher status
```

For an already registered project, confirm that every watched remote context has
an explicit `ssh-key`. If the watcher still needs installation:

```bash
sftpwarden watcher install --watcher docker --dry-run
sftpwarden watcher install --watcher docker
sftpwarden watcher status
```

## Verify the result

Deploy the SFTP project before testing provider delivery. Change an account, then
verify its new access on the remote SFTP server. A running watcher container alone
does not establish successful delivery.

SFTPWarden generates the watcher deployment under its app home. It mounts only
watched project folders, its Docker-specific registry, optional `known_hosts`
and explicit deployment keys. Keys are copied to private temporary paths inside
the container for SSH use. The watcher does not mount the whole `~/.ssh`
directory or the Docker socket.

See [Watcher commands](../../docs/cli/watcher.md) for uninstall behavior and
[Operations](../../docs/operations.md#watcher) for retry and checkpoint details.

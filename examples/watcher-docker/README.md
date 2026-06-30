# Docker Watcher Example

This example shows Docker watcher mode for remote `local-sync` contexts. Docker
watcher mode is managed by the CLI because it depends on registered contexts,
their project roots, and their dedicated SSH keys.

Do not hand-write watcher deployment files or mount the whole SFTPWarden home or
`~/.ssh`; SFTPWarden generates the watcher deployment from context metadata.

Register a remote local-sync context with an explicit deployment key:

```bash
sftpwarden init prod \
  --remote deploy@example.com:/opt/sftpwarden \
  --ssh-key ~/.ssh/sftpwarden_deploy \
  --critical \
  --yes
```

Then install the Docker watcher:

```bash
sftpwarden watcher install --watcher docker --dry-run
sftpwarden watcher install --watcher docker --yes
```

SFTPWarden writes the watcher deployment files under its app home. It mounts only
watched project folders, a Docker-specific context registry, optional
`known_hosts`, and the explicit deployment keys required by the remote contexts.

# Remote-Only Example

Use a remote-only context when the project already lives on a remote Docker host
and you do not want a local editable copy. Your computer stores its connection
details and can request deploy, refresh or health checks over SSH.

## Register the existing project

You need an existing remote `sftpwarden.yaml`, provider storage, Docker Compose
v2 and SSH access with permission to manage the runtime. Replace the example
deployment account, host and path:

```bash
sftpwarden context add archive deploy@example.com:/opt/sftpwarden \
  --remote-only --critical
sftpwarden context show archive
sftpwarden refresh --context archive --dry-run
sftpwarden health --context archive
```

Inspect the registered endpoint before applying any change. A remote-only context
has no local `root` or `config`; registration does not create the remote project.

## Apply changes

Edit accounts through the provider's normal administration workflow or run the
CLI on the server against its project. Then:

```bash
sftpwarden refresh --context archive
```

For configuration changes, preview and deploy:

```bash
sftpwarden deploy --context archive --dry-run
sftpwarden deploy --context archive
```

Deploy prepares the remote project in place using the image matching the CLI
release. The server must be able to obtain that image.

Verify health and a new SFTP connection to the remote server. The watcher is not
used for remote-only contexts. If SSH fails, fix connectivity first; an
unreachable host is not proof that the project was deleted.

See [Operations](../../docs/operations.md#remote-deploy) and
[Recovery](../../docs/recovery.md) for cleanup and maintenance.

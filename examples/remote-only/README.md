# Remote-Only Example

This example shows a remote-only context. The project already lives on the
remote host, and this machine only stores the context metadata needed to refresh
or deploy over SSH.

Create a new remote-only context:

```bash
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical \
  --yes
sftpwarden refresh --context archive --dry-run
```

Remote-only contexts keep local `root` and `config` empty. Refresh runs over SSH
in the remote root.

Use `context add` only when that remote project already exists and you want to
register it locally:

```bash
sftpwarden context add archive deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical \
  --yes
```

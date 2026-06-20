# Remote-Only Example

```bash
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical
```

Remote-only contexts keep local `root` and `config` empty. Refresh runs over SSH in the remote root.

Use `sftpwarden context add archive deploy@example.com:/opt/sftpwarden --remote-only --critical`
only when that remote project already exists and you want to register it locally.

# Remote-Only Example

```bash
sftpwarden context add archive deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical
```

Remote-only contexts keep local `root` and `config` empty. Refresh runs over SSH in the remote root.


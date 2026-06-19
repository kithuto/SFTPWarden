# Remote Contexts

Remote URLs support:

```text
[user@]host[:/remote/path]
```

Examples:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
sftpwarden context add prod example.com --user deploy --remote-root /opt/sftpwarden --critical
sftpwarden context add archive deploy@example.com:/opt/sftpwarden --remote-only --critical
```

When registering remote contexts, SFTPWarden checks SSH connectivity and `docker compose version` unless `--skip-checks` is used.


# Operations

Operational checks:

```bash
sftpwarden doctor
sftpwarden validate
sftpwarden plan -c dev
sftpwarden refresh -c dev --dry-run
```

Runtime state lives in `/var/lib/sftpwarden/state.json`. Host keys should be persisted to avoid changing server fingerprints on restart.


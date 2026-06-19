# CLI

Common commands:

```bash
sftpwarden init dev
sftpwarden info -c dev
sftpwarden validate --config sftpwarden.yaml
sftpwarden plan -c dev
sftpwarden refresh -c dev
sftpwarden users -c dev
```

Flags:

- `--context` / `-c` selects a context.
- `--config` selects a config file.
- `--json` produces machine-readable output where supported.
- `--dry-run` prints commands without executing them where supported.


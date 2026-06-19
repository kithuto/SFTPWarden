# Contexts

Contexts let the CLI know where a SFTPWarden environment lives.

## Local Context

```bash
sftpwarden init dev --root ~/sftpwarden-dev --yes
sftpwarden context use dev
```

A local context stores a project root, config path, provider, and critical flag.

## Remote Local-Sync Context

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical
```

Local files are the source of truth. `sftpwarden watch` syncs config/provider changes to the remote host. Compose changes still require explicit deploy review.

## Remote-Only Context

```bash
sftpwarden context add archive deploy@sftp-archive.example.com:/opt/sftpwarden \
  --remote-only \
  --critical
```

Remote-only contexts store remote paths under the `remote` block and keep top-level `root` and `config` empty. They do not require watcher setup.

## Resolution Order

1. `--config`
2. `--context` / `-c`
3. `SFTPWARDEN_CONTEXT`
4. default context from the registry
5. `sftpwarden.yaml` in the current directory
6. clear error with suggested commands

Production-like names such as `prod`, `production`, `prd`, `live`, and `main` require confirmation unless `--critical` or `--yes` is used.


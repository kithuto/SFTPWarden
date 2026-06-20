# CLI Reference

Common flags:

| Flag | Purpose |
| --- | --- |
| `--context`, `-c` | Select a registered context |
| `--config` | Use a specific `sftpwarden.yaml` |
| `--json` | Emit machine-readable output where supported |
| `--dry-run` | Show planned commands without applying them |
| `--yes`, `-y` | Accept confirmation prompts |

## Main Commands

```bash
sftpwarden init [context-name]
sftpwarden init remote
sftpwarden info
sftpwarden validate
sftpwarden compose
sftpwarden plan
sftpwarden refresh
sftpwarden sync
sftpwarden watch
sftpwarden deploy
sftpwarden doctor
```

## Config Commands

```bash
sftpwarden config show
sftpwarden config show --json
sftpwarden config default-provider
sftpwarden config default-provider yaml
```

## Context Commands

```bash
sftpwarden context add dev
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
sftpwarden context ls
sftpwarden context ls --json
sftpwarden context current
sftpwarden context default dev
sftpwarden context use dev
sftpwarden context show dev
sftpwarden context rename old-name new-name
sftpwarden context remove old-name --yes
sftpwarden context clear
```

## User Commands

```bash
sftpwarden users -c dev
sftpwarden users -c dev --json
sftpwarden user show alice -c dev
sftpwarden user add alice --password "correct horse battery staple" -c dev
sftpwarden user update alice --comment "Finance inbox" -c dev
sftpwarden user remove alice -c dev --yes
```

User mutations trigger `refresh` automatically when they affect runtime state.
Updating only `comment` does not refresh because comments are metadata.

## Watcher Commands

```bash
sftpwarden watcher status
sftpwarden watcher status --json
sftpwarden watcher install --watcher systemd --dry-run
sftpwarden watcher install --watcher docker --yes
sftpwarden watcher uninstall --yes
```

## Runtime Commands

Runtime commands are intended to run inside the container:

```bash
sftpwarden runtime plan --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime refresh --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime sync --config /etc/sftpwarden/sftpwarden.yaml
```

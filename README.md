# SFTPWarden

SFTPWarden is a container-native SFTP gateway for DevOps and Platform Engineering workflows. It combines a lightweight OpenSSH runtime, chroot isolation, declarative user provisioning, and a Typer/Rich CLI for local and remote operations.

The runtime image keeps users and secrets out of the image. User state comes from mounted provider files or configured data sources, host keys live in a persistent volume, and SFTP users are restricted to `internal-sftp`.

## Table of Contents

- [Status](#status)
- [Install for Development](#install-for-development)
- [First Run](#first-run)
- [Local Quickstart](#local-quickstart)
- [Remote Contexts](#remote-contexts)
- [User Management](#user-management)
- [Watch vs Refresh](#watch-vs-refresh)
- [Providers](#providers)
- [Security Model](#security-model)
- [Documentation](#documentation)

## Status

This repository currently implements the foundation for package structure, CLI, global config, context registry, YAML/CSV provider mutation, config validation, Compose generation, watcher target derivation, refresh command routing, and the OpenSSH Docker runtime.

Remote deployment automation, watcher service installation, SQL write strategies, image publishing, and CI publishing are intentionally staged for later milestones.

## Install for Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,watch]"
sftpwarden --version
```

## First Run

Set the global provider once:

```bash
sftpwarden config default-provider yaml
```

Global config is stored at `~/.sftpwarden/config.toml`. Contexts are stored at `~/.sftpwarden/contexts.toml`.

## Local Quickstart

```bash
sftpwarden init dev --root ~/sftpwarden-dev --yes
cd ~/sftpwarden-dev
sftpwarden validate
sftpwarden compose --write
docker compose up -d --build
```

The generated project contains:

- `sftpwarden.yaml`
- `users.yaml` or `users.csv`
- `docker-compose.yml`
- local runtime folders for `data/`, `state/`, and `host_keys/`

The container always listens on port `22` internally. Configure the host port with `server.port`.

## Remote Contexts

Register a remote local-sync context:

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical
```

Register a remote-only context:

```bash
sftpwarden context add archive deploy@sftp-archive.example.com:/opt/sftpwarden \
  --remote-only \
  --critical
```

Remote-only contexts keep top-level `root` and `config` empty in the registry. Remote paths live under the context `remote` block.

## User Management

```bash
sftpwarden user add alice \
  --password "correct horse battery staple" \
  --upload-dir upload

sftpwarden user add bob \
  --password-hash '$y$j9T$...' \
  --upload-dir upload

sftpwarden user update alice \
  --public-key "ssh-ed25519 AAAA..."

sftpwarden users
sftpwarden user show alice
sftpwarden user remove alice --yes
```

Password authentication is enabled by default. If `user add` does not receive `--password`, `--password-hash`, or a key-only config, it prompts for the password and hashes it before saving. `--password-hash` stores an existing system password hash. SSH public keys can be added per user, and password login can be disabled with `auth.allow_password: false` for key-only deployments.

## Watch vs Refresh

`sftpwarden watch` watches remote `local-sync` contexts and syncs provider/config files to the remote host. It ignores Docker Compose files, `.env`, `old/`, data, state, host keys, Git metadata, and Python caches.

`sftpwarden refresh` tells a local or remote runtime to reload users immediately:

```bash
sftpwarden plan -c dev
sftpwarden refresh -c dev
sftpwarden refresh --all
sftpwarden refresh -c prod --dry-run
```

## Providers

| Provider | Runtime reads | CLI mutations | Notes |
| --- | --- | --- | --- |
| YAML | Yes | Yes | Default provider |
| CSV | Yes | Yes | Public keys are newline-separated in the CSV field |
| MySQL | Planned | Planned with explicit write strategy | Requires DSN |
| PostgreSQL | Planned | Planned with explicit write strategy | Requires DSN |

## Security Model

- Users and secrets are never baked into Docker images.
- `.env` is ignored unless explicitly included by a future remote workflow.
- SFTP users use `ChrootDirectory /data/%u` and `ForceCommand internal-sftp`.
- SSH forwarding, tunneling, X11 forwarding, root login, and user environments are disabled.
- Host keys, user data, and runtime UID/GID state are persisted outside the image.
- User removal disables access but does not delete user data.
- Watcher and remote workflows do not require Docker socket access.
- Remote `ssh_key: default` uses the server account's normal SSH identity resolution instead of forcing an identity file.

## Documentation

- [Configuration](docs/configuration.md)
- [Contexts](docs/contexts.md)
- [Runtime](docs/runtime.md)
- [Security](docs/security.md)

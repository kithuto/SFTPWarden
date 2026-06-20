# SFTPWarden

Container-native SFTP gateway powered by OpenSSH, chroot isolation, declarative user
providers, and a practical CLI for local and remote operations.

[Installation](#installation) | [Quick Start](#quick-start) | [Users](#user-management) |
[Contexts](#contexts) | [Providers](#providers) | [Operations](#operations) |
[Security](#security-model) | [Documentation](#documentation)

---

SFTPWarden runs a small OpenSSH-based container and keeps users, secrets, host keys,
and runtime state outside the image. Operators manage environments with the
`sftpwarden` CLI, while the runtime applies user changes from YAML, CSV, MySQL, or
PostgreSQL providers.

Key features:

- Lightweight OpenSSH runtime image with no baked-in users.
- Per-user SFTP chroot under `/data/<username>`.
- YAML, CSV, MySQL, and PostgreSQL providers.
- CLI user management with password hashing and SSH key support.
- Automatic UID/GID allocation with persistent runtime state.
- Local, remote `local-sync`, and remote `remote-only` contexts.
- Remote deploy through SSH, rsync, and Docker Compose.
- Watcher support for syncing editable config/provider files to remote hosts.
- Immediate runtime reloads with `sftpwarden refresh`.
- JSON output for automation-friendly commands.

> Security note: SFTPWarden provides OpenSSH chroot isolation inside a container.
> It is not VM-grade isolation and does not replace host hardening.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Project Files](#project-files)
- [User Management](#user-management)
- [Contexts](#contexts)
- [Watch vs Refresh](#watch-vs-refresh)
- [Providers](#providers)
- [Configuration](#configuration)
- [Operations](#operations)
- [Security Model](#security-model)
- [Documentation](#documentation)
- [Development](#development)

## Installation

Development install:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,docs,watch,mysql,postgres]"
sftpwarden --version
```

Optional provider extras:

| Provider | Extra |
| --- | --- |
| MySQL | `pip install -e ".[mysql]"` |
| PostgreSQL | `pip install -e ".[postgres]"` |
| Watcher tooling | `pip install -e ".[watch]"` |

Build the runtime image locally:

```bash
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
```

Build the watcher image locally:

```bash
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
```

## Quick Start

Create a local environment:

```bash
sftpwarden config default-provider yaml
sftpwarden init dev --root ~/sftpwarden-dev --yes
cd ~/sftpwarden-dev
sftpwarden validate
sftpwarden compose --write
docker compose up -d --build
```

Add a user and force the runtime to reload:

```bash
sftpwarden user add alice \
  --password "correct horse battery staple" \
  --comment "Main upload account" \
  -c dev

sftpwarden refresh -c dev
```

Preview runtime changes:

```bash
sftpwarden plan -c dev
sftpwarden runtime plan --config /etc/sftpwarden/sftpwarden.yaml
```

## Project Files

`sftpwarden init` creates a small project directory:

```text
sftpwarden.yaml
users.yaml              # or users.csv
docker-compose.yml
data/
state/
host_keys/
```

The container always listens on port `22` internally. Configure the host-facing
port with `server.port` in `sftpwarden.yaml`.

The canonical runtime Dockerfile is `docker/runtime/Dockerfile`. The top-level
`Dockerfile` remains as a compatibility entrypoint.

## User Management

List and inspect users:

```bash
sftpwarden users -c dev
sftpwarden user show alice -c dev
```

Add users:

```bash
sftpwarden user add alice --password "correct horse battery staple" -c dev
sftpwarden user add bob --password-hash '$6$rounds=500000$...' -c dev
sftpwarden user add carol --public-key "ssh-ed25519 AAAA..." -c dev
```

Update users:

```bash
sftpwarden user update alice --upload-dir inbound -c dev
sftpwarden user update alice --uid 12001 --gid 12001 -c dev
sftpwarden user update alice --comment "Finance inbox" -c dev
sftpwarden user update alice --disabled -c dev
```

Remove a user from the provider:

```bash
sftpwarden user remove alice -c dev --yes
```

Removing a user disables access but does not delete user data.

## Contexts

Contexts tell the CLI where an environment lives.

Local context:

```bash
sftpwarden init dev --root ~/sftpwarden-dev --yes
sftpwarden context use dev
```

Remote local-sync context:

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical \
  --skip-checks
```

Remote-only context:

```bash
sftpwarden context add archive deploy@sftp-archive.example.com:/opt/sftpwarden \
  --remote-only \
  --critical \
  --skip-checks
```

Context resolution order:

1. `--config`
2. `--context` / `-c`
3. `SFTPWARDEN_CONTEXT`
4. default context from `~/.sftpwarden/contexts.toml`
5. `sftpwarden.yaml` in the current directory

Production-like names such as `prod`, `production`, `prd`, `live`, and `main`
require confirmation unless marked with `--critical` or accepted with `--yes`.

## Watch vs Refresh

`watch` syncs editable local files for remote `local-sync` contexts:

```bash
sftpwarden sync --json
sftpwarden watch
```

Watched files are derived from the context registry and each context's
`sftpwarden.yaml`. For YAML/CSV providers this means the project config and the
configured provider file. For SQL providers, only the project config is synced.

`refresh` tells a running runtime to reload users immediately:

```bash
sftpwarden refresh -c dev
sftpwarden refresh --all
sftpwarden refresh -c prod --dry-run --json
```

Docker Compose changes require an explicit deploy:

```bash
sftpwarden deploy -c prod --dry-run
sftpwarden deploy -c prod --yes
```

## Providers

| Provider | Runtime reads | CLI mutations | Notes |
| --- | ---: | ---: | --- |
| YAML | Yes | Yes | Default provider and best quickstart path |
| CSV | Yes | Yes | Public keys are newline-separated in one field |
| MySQL | Yes | Yes | Requires `dsn`; uses the configured table/query |
| PostgreSQL | Yes | Yes | Requires `dsn`; uses the configured table/query |

SQL providers read from `sftp_users` by default. The table should include:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

See `examples/mysql/schema.sql` and `examples/postgres/schema.sql`.

## Configuration

Minimum valid `sftpwarden.yaml`:

```yaml
version: 1
project:
  name: sftpwarden
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
```

Useful defaults:

```yaml
server:
  port: 2222

auth:
  allow_public_key: true
  allow_password: true
  recommended: password

sync:
  interval_seconds: 60
  disable_missing_users: true
```

`server.container_port` is intentionally invalid. The container SSH port is
always `22`; `server.port` controls the host port exposed by Docker Compose.

Global CLI config lives at `~/.sftpwarden/config.toml`. Contexts live at
`~/.sftpwarden/contexts.toml`.

## Operations

Common operational commands:

```bash
sftpwarden doctor
sftpwarden validate --config sftpwarden.yaml
sftpwarden compose --write
sftpwarden plan -c dev --json
sftpwarden refresh -c dev --dry-run
sftpwarden watcher status --json
```

Runtime state is stored in `/var/lib/sftpwarden/state.json` inside the container
and should be backed by the `state/` volume. Host keys are stored in `host_keys/`
to keep SSH server fingerprints stable across restarts.

## Security Model

SFTPWarden follows conservative defaults:

- Users and secrets are not baked into images.
- Plaintext passwords are rejected in provider data.
- `sftpwarden user add --password` stores only a system password hash.
- SFTP users are forced into `internal-sftp`.
- Root login, empty passwords, forwarding, tunneling, X11, and user environments
  are disabled.
- User data is not deleted automatically.
- `.env`, `data/`, `state/`, `host_keys/`, Git metadata, and Python caches are
  not watched or synced by the watcher.
- Docker watcher mode does not require Docker socket access.

For key-only deployments, add public keys to every active user and set:

```yaml
auth:
  allow_public_key: true
  allow_password: false
  recommended: public_key
```

## Documentation

General documentation lives in this README. Specific guides live in:

- [Configuration](docs/configuration.md)
- [Operations](docs/operations.md)
- [Security](docs/security.md)
- [CLI Reference](docs/cli-reference.md)

The Sphinx site is built from `docs/` and published to GitHub Pages by the docs
workflow.

Local docs build:

```bash
python -m pip install -e ".[docs]"
sphinx-build -b html docs docs/_build/html
```

## Development

Run the test suite:

```bash
python -m pip install tox
tox
```

`tox` expects Python 3.11, 3.12, and 3.13 to be available on the machine. It
creates one environment per Python version and installs the test dependencies
there.

Run a single Python version:

```bash
tox -e py311
tox -e py312
tox -e py313
```

Release automation for v1.0 is expected to publish the Python package to PyPI,
runtime and watcher images to GHCR, and documentation to GitHub Pages.

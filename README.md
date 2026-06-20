<p align="center">
  <img
    src="https://raw.githubusercontent.com/kithuto/SFTPWarden/main/docs/_static/logo-sftpwarden.png"
    alt="SFTPWarden - Container-native SFTP management"
    width="760"
  >
</p>

<p align="center">
  Container-native SFTP for teams that want a small, auditable OpenSSH runtime with
  declarative users, predictable Docker deployment, and a CLI that works the same
  locally and on remote hosts.
</p>

<p align="center">
  <a href="#key-features">Key Features</a>
  &nbsp;·&nbsp;
  <a href="#installation">Installation</a>
  &nbsp;·&nbsp;
  <a href="#5-minute-quick-start">Quick Start</a>
  &nbsp;·&nbsp;
  <a href="#deployment-choices">Deployment</a>
  &nbsp;·&nbsp;
  <a href="#providers">Providers</a>
  &nbsp;·&nbsp;
  <a href="#documentation">Docs</a>
  &nbsp;·&nbsp;
  <a href="#contributing">Contributing</a>
</p>

---

SFTPWarden runs OpenSSH in a container and keeps users, host keys, data, and runtime
state outside the image. You manage environments with `sftpwarden`, and the runtime
keeps Linux users synchronized from YAML, CSV, MySQL, or PostgreSQL.

## Key Features

- **Fast adoption for real SFTP needs:** create a local or remote SFTP environment
  with `sftpwarden init`, add users, and deploy with Docker Compose without
  hand-writing OpenSSH container plumbing.
- **Declarative user sources:** manage accounts from YAML, CSV, MySQL, or
  PostgreSQL, so small teams can start with files and larger systems can use SQL.
- **Safe user isolation:** every SFTP user is forced into OpenSSH `internal-sftp`
  and isolated under `/data/<username>` with chroot-oriented defaults.
- **Docker-native operations:** generated Compose files, `sftpwarden deploy`,
  `plan`, `refresh`, `watch`, `--dry-run`, and `--json` make it practical for
  local development, CI, and production runbooks.
- **Context-based workflow:** use Docker-style active contexts for `dev`, `prod`,
  remote local-sync, and remote-only deployments instead of repeating long flags
  on every command.
- **Remote deployment built in:** deploy through SSH, rsync, and Docker Compose,
  with systemd or Docker watcher modes for syncing user-provider changes.
- **Operationally conservative defaults:** secrets are not baked into images,
  plaintext provider passwords are rejected, host keys and state are persisted,
  and user data is never deleted unless explicitly requested.

SFTPWarden is intentionally lightweight. It is not a full identity platform, a file
sharing suite, or VM-grade isolation. It gives you a conservative OpenSSH-based SFTP
runtime that is easy to understand, deploy, and operate.

## Table of Contents

- [Key Features](#key-features)
- [Installation](#installation)
- [Shell Autocomplete](#shell-autocomplete)
- [5-Minute Quick Start](#5-minute-quick-start)
- [Deployment Choices](#deployment-choices)
- [Project Files](#project-files)
- [User Management](#user-management)
- [Providers](#providers)
- [Operations](#operations)
- [Security](#security)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Installation

Install the CLI:

```bash
pip install sftpwarden
sftpwarden --version
```

For local development:

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[watch,mysql,postgres]"
sftpwarden --version
```

Optional extras:

| Need | Install |
| --- | --- |
| File watcher support | `pip install "sftpwarden[watch]"` |
| MySQL provider | `pip install "sftpwarden[mysql]"` |
| PostgreSQL provider | `pip install "sftpwarden[postgres]"` |
| Documentation/development | `pip install -e ".[dev,docs,watch,mysql,postgres]"` |

Build the runtime image locally:

```bash
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
```

## Shell Autocomplete

SFTPWarden can install shell autocomplete through the Typer/Click helpers included
in the CLI:

```bash
sftpwarden --install-completion
```

Open a new terminal, then use `<TAB>` to complete commands and options:

```bash
sftpwarden con<TAB>
sftpwarden user add --<TAB>
```

To inspect the generated completion script without installing it:

```bash
sftpwarden --show-completion
```

## 5-Minute Quick Start

Create a local SFTP project:

```bash
sftpwarden config default-provider yaml
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
sftpwarden validate
sftpwarden deploy
```

Add a user:

```bash
sftpwarden user add alice \
  --password "correct horse battery staple" \
  --comment "Main upload account"
```

Connect with any SFTP client:

```bash
sftp -P 2222 alice@localhost
```

Preview and apply runtime changes:

```bash
sftpwarden plan
sftpwarden refresh
```

`sftpwarden init` makes the created context active, so day-to-day commands do not
need `--context`. This follows the same friendly idea people know from Docker:
work in a project directory, keep an active context, and pass an explicit context
only when you need to override it. To switch later, run `sftpwarden context use dev`.

Read or update project settings without opening YAML:

```bash
sftpwarden config project.name dev2
sftpwarden config server.port 2200
sftpwarden context root ~/sftpwarden-dev2 --yes
```

## Deployment Choices

Pick the model that matches how your team works.

| Model | Best for | Source of truth | Watcher |
| --- | --- | --- | --- |
| Local | Development, demos, single-host testing | Local project folder | No |
| Remote local-sync | Production managed from a workstation or CI runner | Local project folder synced to remote host | Yes |
| Remote-only | Existing remote deployments managed in-place | Remote project folder | No |

Local:

```bash
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
sftpwarden deploy
```

Remote local-sync:

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@sftp-prod.example.com:/opt/sftpwarden \
  --critical

sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Remote-only:

```bash
sftpwarden init archive --remote deploy@sftp-archive.example.com:/opt/sftpwarden \
  --remote-only \
  --critical

sftpwarden refresh --dry-run
```

Use `sftpwarden context add` when a SFTPWarden project already exists and you only
want to register it on this machine:

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden --critical
sftpwarden context use prod
```

Production-like names such as `prod`, `production`, `prd`, `live`, and `main`
require confirmation unless marked with `--critical` or accepted with `--yes`.

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

Runtime state is stored in `/var/lib/sftpwarden/state.json` inside the container
and should be backed by the `state/` volume. Host keys are stored in `host_keys/`
to keep SSH fingerprints stable across restarts.

## User Management

List and inspect users:

```bash
sftpwarden users
sftpwarden user show alice
```

Add users:

```bash
sftpwarden user add alice --password "correct horse battery staple"
sftpwarden user add bob --password-hash '$6$rounds=500000$...'
sftpwarden user add carol --public-key "ssh-ed25519 AAAA..."
```

Update users:

```bash
sftpwarden user update alice --upload-dir inbound
sftpwarden user update alice --uid 12001 --gid 12001
sftpwarden user update alice --comment "Finance inbox"
sftpwarden user update alice --disabled
```

Removing a user disables access but does not delete user data:

```bash
sftpwarden user remove alice --yes
```

To permanently remove the user's data directory too:

```bash
sftpwarden user remove alice --delete-files --yes
```

Updating only `comment` does not refresh the runtime because comments are metadata.

## Providers

| Provider | Runtime reads | CLI mutations | Good fit |
| --- | ---: | ---: | --- |
| YAML | Yes | Yes | Quick start, GitOps-style small deployments |
| CSV | Yes | Yes | Spreadsheet-friendly user handoff |
| MySQL | Yes | Yes | Existing application databases |
| PostgreSQL | Yes | Yes | Existing platform or product databases |

SQL providers read from `sftp_users` by default. The table should include:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

See `examples/mysql/schema.sql` and `examples/postgres/schema.sql`.

During `init`, SFTPWarden checks whether the configured SQL table exists. If it is
missing, it asks whether to create the table or abort so you can create it
manually:

```bash
sftpwarden init prod \
  --provider postgresql \
  --dsn '${SFTPWARDEN_POSTGRES_DSN}' \
  --create-table
```

## Operations

Common operational commands:

```bash
sftpwarden doctor
sftpwarden validate --config sftpwarden.yaml
sftpwarden compose --write
sftpwarden deploy --dry-run
sftpwarden plan --json
sftpwarden refresh --dry-run
sftpwarden watcher status --json
```

`watch` and `refresh` are different on purpose:

- `sftpwarden watch` syncs YAML/CSV user provider files for remote `local-sync` contexts.
- `sftpwarden refresh` tells a running runtime to reload users immediately.
- Configuration and Docker Compose changes require `sftpwarden deploy`.

`sftpwarden deploy` checks for Docker Compose before starting local deployments.
For remote deployments, the remote host must have Docker Compose v2 available as
`docker compose`.

For production watcher installs, prefer `systemd` so SSH uses the host's normal
identity, agent, `~/.ssh/config`, known hosts, bastions, and `ProxyJump` settings.
Docker watcher mode is stricter and requires explicit dedicated deployment keys.

## Security

SFTPWarden follows conservative defaults:

- users and secrets are not baked into images;
- plaintext passwords are rejected in provider data;
- `sftpwarden user add --password` stores only a system password hash;
- SFTP users are forced into `internal-sftp`;
- root login, empty passwords, forwarding, tunneling, X11, and user environments
  are disabled;
- user data is not deleted automatically;
- `sftpwarden user remove --delete-files` is explicit and irreversible;
- `.env`, `data/`, `state/`, `host_keys/`, Git metadata, and Python caches are not
  watched or synced.

Key-only deployment:

```yaml
auth:
  allow_public_key: true
  allow_password: false
  recommended: public_key
```

Read the [security guide](docs/security.md) before exposing a deployment to a
public or customer-facing network.

## Documentation

The README is the adoption path. Detailed guides live in:

- [Configuration](docs/configuration.md)
- [Operations](docs/operations.md)
- [Security](docs/security.md)
- [CLI Reference](docs/cli-reference.md)
- [Contributing, development, and testing](docs/contributing.md)

The Sphinx documentation is built from `docs/` and published to GitHub Pages.

Build it locally:

```bash
python -m pip install -e ".[docs]"
sphinx-build -b html docs docs/_build/html
```

## Roadmap

### v1.1 - Kubernetes

- Add Helm chart and Kubernetes manifests.
- Add ConfigMap/Secret/PVC examples.
- Add liveness/readiness probes.

## Contributing

Contributions are welcome: bug reports, docs fixes, examples, tests, provider work,
and operational feedback are all useful.

Contribution workflow:

1. Fork the repository.
2. Create your own branch from `dev`.
3. Develop and validate your change in that branch.
4. Open a Pull Request from your branch to `dev`.

Normal contribution PRs should target `dev`, not `main`. The maintainer promotes
accepted changes from `dev` to `main` for production and release work.

Start here:

- [CONTRIBUTING.md](https://github.com/kithuto/sftpwarden/blob/dev/CONTRIBUTING.md)
  for the GitHub workflow.
- [docs/contributing.md](docs/contributing.md) for install, development, testing,
  docs, and release checks.
- [SECURITY.md](https://github.com/kithuto/sftpwarden/blob/dev/SECURITY.md) for
  responsible vulnerability reporting.
- [CODE_OF_CONDUCT.md](https://github.com/kithuto/sftpwarden/blob/dev/CODE_OF_CONDUCT.md)
  for participation expectations.

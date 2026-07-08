<p align="center">
  <img
    src="https://raw.githubusercontent.com/kithuto/SFTPWarden/main/docs/_static/logo-sftpwarden.png"
    alt="SFTPWarden - Container-native SFTP management"
    width="760"
  >
</p>

<p align="center">
  Container-native SFTP for teams that want a small, auditable OpenSSH runtime with
  declarative users, predictable Compose/Kubernetes deployment, and a CLI that
  works the same locally, on remote hosts, and in clusters.
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
keeps Linux users synchronized from YAML, CSV, SQLite, MySQL, MariaDB,
PostgreSQL, or MongoDB.

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
- [Backup and Restore](#backup-and-restore)
- [Security](#security)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Key Features

- **Fast adoption for real SFTP needs:** create a local, remote, or Kubernetes
  SFTP environment with `sftpwarden init`, add users, and deploy without
  hand-writing OpenSSH container plumbing.
- **Declarative user sources:** manage accounts from YAML, CSV, SQLite, MySQL,
  MariaDB, PostgreSQL, or MongoDB, so small teams can start with files and larger
  systems can use databases.
- **Safe user isolation:** every SFTP user is forced into OpenSSH `internal-sftp`
  and isolated under `/data/<username>` with chroot-oriented defaults.
- **Container-native operations:** generated Compose files, Kubernetes manifests,
  Helm values, `sftpwarden deploy`, `plan`, `refresh`, `watch`, `--dry-run`, and
  `--json` make it practical for local development, CI, and production runbooks.
- **First-class Kubernetes and Helm support:** generate manifests or Helm values,
  manage namespaces, PVCs, runtime probes, and declarative YAML/CSV provider
  syncs while keeping database providers recommended for production clusters.
- **Context-based workflow:** use Docker-style active contexts for `dev`, `prod`,
  remote local-sync, and remote-only deployments instead of repeating long flags
  on every command.
- **Remote deployment built in:** deploy through SSH, rsync/scp, and Docker
  Compose, with auto-detected watcher backends for syncing user-provider changes.
- **Portable operations:** copy users between providers, export/import user
  snapshots, create backups, restore safely, and run project/runtime healthchecks.
- **Operationally conservative defaults:** secrets are not baked into images,
  plaintext provider passwords are rejected, host keys and state are persisted,
  and user data is never deleted unless explicitly requested.

SFTPWarden is intentionally lightweight. It is not a full identity platform, a file
sharing suite, or VM-grade isolation. It gives you a conservative OpenSSH-based SFTP
runtime that is easy to understand, deploy, and operate.

## Installation

Install the CLI:

```bash
pip install sftpwarden
sftpwarden --version
```

For source checkout usage:

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[mysql,postgres,mongodb]"
sftpwarden --version
```

Optional extras:

| Need | Install |
| --- | --- |
| SQLite provider | Included, no extra |
| MySQL provider | `pip install "sftpwarden[mysql]"` |
| MariaDB provider | `pip install "sftpwarden[mariadb]"` |
| PostgreSQL provider | `pip install "sftpwarden[postgres]"` |
| MongoDB provider | `pip install "sftpwarden[mongodb]"` |
| Documentation/development | `pip install -e ".[dev,docs,mysql,postgres,mongodb]"` |

`mariadb` is an alias of the MySQL extra. Installing either
`sftpwarden[mysql]` or `sftpwarden[mariadb]` enables both MySQL and MariaDB
providers because they share PyMySQL.

For runtime and watcher image development, build the images locally:

```bash
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
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
sftpwarden user create --<TAB>
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
sftpwarden init dev --user-schema 1 --yes
sftpwarden validate
sftpwarden deploy
```

The quick start uses **user schema v1**, the simplest provider format:
users store anonymous `public_keys` directly on each user. New projects default
to schema v2 when `--user-schema` is omitted; v2 adds named keys, metadata,
expiry, disable/enable, rotation, and migrations.

Add a user:

```bash
sftpwarden user create alice \
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
| Kubernetes | Platform/SRE teams using `kubectl` or Helm | Project config plus generated manifests or Helm values | No |

Compose is the default when `--deploy` is omitted. Choose the deployment target
explicitly during `init` when you already know where the project will run:

```bash
sftpwarden init dev --deploy compose --yes
sftpwarden init prod --deploy kube --yes
sftpwarden init prod --deploy helm --yes
```

If a project was initialized with the default and you want to change it before
deploying, update the project config and preview the generated deployment:

```bash
sftpwarden config deploy.target kubernetes
sftpwarden config kubernetes.mode helm
sftpwarden deploy --dry-run
```

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

Kubernetes manifests:

```bash
sftpwarden init prod --deploy kube --yes
sftpwarden kube render
sftpwarden deploy --dry-run
```

Helm:

```bash
sftpwarden init prod --deploy helm --yes
sftpwarden helm values --write
sftpwarden helm template
sftpwarden deploy --dry-run
```

During Kubernetes or Helm init, SFTPWarden checks the namespace. If it does not
exist, interactive init asks whether to create it; `--yes` creates it
automatically. The default namespace is `sftpwarden`; use `--namespace <name>`
for an existing or custom namespace, or `--no-create-namespace` to require it to
exist already.

For Kubernetes projects that use YAML or CSV providers, the local provider file
is declarative: `sftpwarden deploy`, `sftpwarden kube apply`, and
`sftpwarden helm upgrade` render its current contents and copy them into the
provider PVC during the runtime rollout. `refresh` reloads users already visible
inside the runtime; it does not copy local YAML/CSV files into a cluster by
itself. Treat Kubernetes YAML/CSV provider files as deployment material because
the rendered manifests or Helm values include their user entries. For production
Kubernetes, prefer database providers when user state must change outside deploy
cycles or when provider data should not be carried in manifests.

Kubernetes and Helm projects reserve `10Gi` for SFTP user uploads by default.
Increase that PVC before deploying with:

```bash
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Compose healthcheck timing and Kubernetes probe timing are configurable too:
use `healthcheck.*` for Compose and `kubernetes.*_probe.*` for generated
manifests or Helm values.

Source checkouts use the local chart. Python package installations use the
published GHCR OCI chart with the same version as the installed CLI.

Use `sftpwarden context add` when a SFTPWarden project already exists and you only
want to register it on this machine:

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden --critical
sftpwarden context use prod
```

Production-like names such as `prod`, `production`, `prd`, `live`, and `main`
require confirmation unless marked with `--critical` or accepted with `--yes`.

## Project Files

By default, `sftpwarden init` creates a Compose-backed project directory:

```text
sftpwarden.yaml
users.yaml              # or users.csv / users.sqlite
docker-compose.yml
data/
state/
host_keys/
```

Kubernetes-targeted projects use the same `sftpwarden.yaml`, plus generated
`kubernetes.yml` or `values.yaml` when you render/apply manifests or Helm values;
they do not require `docker-compose.yml`.

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
sftpwarden user create alice --password "correct horse battery staple"
sftpwarden user create bob --password-hash '$6$rounds=500000$...'
sftpwarden user create carol --public-key "ssh-ed25519 AAAA..."
```

Update users:

```bash
sftpwarden user update alice --upload-dir inbound
sftpwarden user update alice --uid 12001 --gid 12001
sftpwarden user update alice --comment "Finance inbox"
sftpwarden user disable alice
sftpwarden user enable alice
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

### User Schemas and Named Keys

SFTPWarden supports two user schemas:

- **Schema v1** keeps simple `public_keys` on each user. It is supported for
  small setups and quick starts.
- **Schema v2** stores named `keys` with fingerprint, comment, disabled state,
  timestamps, expiry, source, and metadata. It is the default for new projects.

Choose explicitly during init:

```bash
sftpwarden init demo --user-schema 1 --yes
sftpwarden init prod --user-schema 2 --yes
```

Named key lifecycle commands:

```bash
sftpwarden user key list alice
sftpwarden user key show alice prod-ci
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub
sftpwarden user key rotate alice prod-ci --public-key ./prod-ci-new.pub
sftpwarden user key expire alice prod-ci --at 2027-01-01
sftpwarden user key disable alice prod-ci
sftpwarden user key enable alice prod-ci
sftpwarden user key rename alice prod-ci ci-prod
sftpwarden user key remove alice ci-prod --yes
sftpwarden user key import alice --from-dir ./keys
```

Schema v1 can still list, show, add, and remove anonymous keys using
deterministic names/fingerprints. Advanced key operations prompt to migrate that
provider to schema v2; non-interactive use requires `--yes`.

Inspect or migrate provider data explicitly:

```bash
sftpwarden provider schema show
sftpwarden provider keys migrate --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

## Providers

| Provider | Runtime reads | CLI mutations | Good fit |
| --- | ---: | ---: | --- |
| YAML | Yes | Yes | Quick start, GitOps-style small deployments |
| CSV | Yes | Yes | Spreadsheet-friendly user handoff |
| SQLite | Yes | Yes | Single-host/self-hosted deployments without an external database |
| MySQL | Yes | Yes | Existing application databases |
| MariaDB | Yes | Yes | MySQL-compatible MariaDB deployments |
| PostgreSQL | Yes | Yes | Existing platform or product databases |
| MongoDB | Yes | Yes | Existing document databases |

For production Kubernetes environments, prefer PostgreSQL, MariaDB/MySQL, or
MongoDB. The runtime reads those providers directly, so external provider changes
can be picked up by the runtime sync loop or an explicit `sftpwarden refresh`.
YAML/CSV remain useful for GitOps-style Kubernetes deployments where deploy is
the synchronization point. If you use YAML/CSV in Kubernetes, keep the provider
file in the same review and secret-handling process as the generated manifests.

SQL providers read from `sftp_users` by default. Schema v1 uses only the users
table:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

Schema v2 keeps the users table and adds `sftp_user_keys` for named key rows.
CSV v2 uses a `keys` JSON column; MongoDB v2 embeds `keys` in each document.

See `examples/mysql/schema.sql`, `examples/mariadb/schema.sql`, and
`examples/postgres/schema.sql` for schema v2 SQL tables.

SQLite is built in:

```bash
sftpwarden init dev --provider sqlite --yes
```

SQLite is a good lightweight option for one host and one writer. Avoid it for NFS,
high-concurrency, or multi-writer deployments.

During `init`, SFTPWarden checks whether external provider storage exists. For
MySQL, MariaDB, and PostgreSQL that means the configured SQL table. For MongoDB
that means the configured collection and username index. If storage is missing,
interactive init asks whether to create it or abort so you can create it manually:

```bash
sftpwarden init prod \
  --provider postgresql \
  --dsn 'postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden' \
  --create-table
```

MariaDB uses the same compatible implementation as MySQL:

```bash
sftpwarden init prod \
  --provider mariadb \
  --dsn 'mariadb://sftpwarden:change-me@db.example.com:3306/sftpwarden' \
  --create-table
```

MongoDB stores one document per user with `_id = username`:

```bash
sftpwarden init prod \
  --provider mongodb \
  --dsn 'mongodb://mongo.example.com:27017/sftpwarden' \
  --collection sftp_users
```

`--dsn` uses the standard database URL/DSN convention:

```text
postgresql://user:password@host:5432/database
mysql://user:password@host:3306/database
mariadb://user:password@host:3306/database
mongodb://user:password@host:27017/database
```

For real environments, prefer an environment variable so the secret is not typed
directly in shell history or committed in project files:

```bash
export SFTPWARDEN_POSTGRES_DSN='postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden'

sftpwarden init prod \
  --provider postgresql \
  --dsn '${SFTPWARDEN_POSTGRES_DSN}' \
  --create-table
```

If you run interactive `init` with MySQL, MariaDB, or PostgreSQL and omit
`--dsn`, SFTPWarden asks for host, port, database, username, and password, then
writes the equivalent DSN for you. For MongoDB, interactive init asks for the
MongoDB DSN directly.

## Operations

Common operational commands:

```bash
sftpwarden doctor
sftpwarden validate --config sftpwarden.yaml
sftpwarden compose --write
sftpwarden deploy --dry-run
sftpwarden deploy --json --dry-run
sftpwarden kube status --json
sftpwarden helm lint
sftpwarden plan --json
sftpwarden refresh --dry-run
sftpwarden watcher status --json
sftpwarden health --json
sftpwarden backup --output sftpwarden-dev.tar.gz --yes
sftpwarden provider export --format json > users.json
```

`watch` and `refresh` are different on purpose:

- `sftpwarden watch` syncs YAML/CSV/SQLite user provider files for remote
  `local-sync` contexts.
- `sftpwarden refresh` tells a running runtime to reload users immediately.
- Configuration, Docker Compose, Kubernetes, and Helm changes require
  `sftpwarden deploy`; Kubernetes YAML/CSV provider files are also copied to the
  provider PVC by deploy/apply/upgrade.
- `sftpwarden.yaml` is desired state. Changes made with `sftpwarden config` and
  manual edits are applied by the next deploy/apply/upgrade step.
- `sftpwarden health` validates config, provider readability, Compose drift, and
  runtime health where available.
- `sftpwarden backup` stores config, a provider user snapshot, host keys, and
  runtime state. It excludes `data/` unless `--include-data` is explicitly used.
- `sftpwarden provider copy` moves users between contexts/providers with explicit
  `--merge` or `--replace` semantics.

`sftpwarden deploy` uses the configured deployment target. Compose remains the
default. Kubernetes manifest mode uses `kubectl`, and Helm mode uses `helm`.
Missing tools are reported with actionable messages.

Watcher installs default to `auto`. SFTPWarden detects the host scheduler and
uses Windows Task Scheduler, macOS launchd, or Linux systemd, OpenRC, runit, or
supervisord when available. You can force a backend with `--watcher systemd`,
`--watcher openrc`, `--watcher runit`, `--watcher supervisord`,
`--watcher launchd`, `--watcher windows-task`, or `--watcher docker`.

Native watcher modes use the host's normal SSH identity, agent, `~/.ssh/config`,
known hosts, bastions, and `ProxyJump` settings. Docker watcher mode is stricter
and requires explicit dedicated deployment keys. It mounts watched project
folders read-only and copies mounted keys inside the container with private
permissions before syncing. Source checkouts use `sftpwarden-watcher:local`;
Python package installations use
`ghcr.io/kithuto/sftpwarden-watcher:<installed-version>` unless `--image` is set.

## Backup and Restore

Back up the active context before upgrades, provider migrations, or risky
configuration changes:

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
```

The default backup includes `sftpwarden.yaml`, generated deployment files,
available file-backed provider data, `provider/users.json` with the current
users read from the provider, host keys, and runtime state. That user snapshot is
also created for SQL and MongoDB providers when the CLI can reach the configured
database. The backup does not include uploaded SFTP user files under `data/`
unless you ask for that explicitly:

```bash
sftpwarden backup --include-data --output sftpwarden-prod-full.tar.gz
```

Preview backup contents in automation with:

```bash
sftpwarden backup --dry-run --json
```

Restore into the active context with:

```bash
sftpwarden restore sftpwarden-prod.tar.gz --yes
```

Use `--include-data` only when the archive was created with user data and you
intend to overwrite the current `data/` tree:

```bash
sftpwarden restore sftpwarden-prod-full.tar.gz --include-data --yes
```

Restore creates a safety backup before overwriting files. After restoring, review
and apply the deployment so the running runtime matches the restored project:

```bash
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Backup archives can contain DSNs, provider snapshots, host keys, and uploaded
user files when `--include-data` is used. Store them like infrastructure secrets.

## Security

SFTPWarden follows conservative defaults:

- users and secrets are not baked into images;
- plaintext passwords are rejected in provider data;
- `sftpwarden user create --password` stores only a system password hash;
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

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [Providers](docs/providers.md)
- [Named Keys](docs/named-keys.md)
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

See the [changelog](https://github.com/kithuto/sftpwarden/blob/main/CHANGELOG.md)
for released versions and the longer future roadmap.

### v1.4 - Audit and Transfer Visibility

- Add audit logging for CLI and runtime operations.
- Add commands for listing, tailing, filtering, and exporting audit events.
- Add transfer visibility and richer runtime status.

### v1.5 - Security Hardening

- Add production-oriented security check profiles and strict mode.
- Add secret-file support for DSNs and sensitive provider settings.
- Add assisted host key rotation workflows.

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

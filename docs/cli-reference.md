# CLI Reference

This page explains the `sftpwarden` command line for people who are new to the
project. You do not need to know SFTPWarden internals to use these commands.

## Table of Contents

- [How the CLI Thinks](#how-the-cli-thinks)
- [Common Flags](#common-flags)
- [Shell Autocomplete](#shell-autocomplete)
- [Main Commands](#main-commands)
  - [sftpwarden init](#sftpwarden-init)
  - [Database init flags](#database-init-flags)
  - [sftpwarden deploy](#sftpwarden-deploy)
  - [sftpwarden validate](#sftpwarden-validate)
  - [sftpwarden compose](#sftpwarden-compose)
  - [sftpwarden plan](#sftpwarden-plan)
  - [sftpwarden refresh](#sftpwarden-refresh)
  - [sftpwarden sync](#sftpwarden-sync)
  - [sftpwarden watch](#sftpwarden-watch)
  - [sftpwarden info](#sftpwarden-info)
  - [sftpwarden doctor](#sftpwarden-doctor)
  - [sftpwarden health](#sftpwarden-health)
  - [sftpwarden backup](#sftpwarden-backup)
  - [sftpwarden restore](#sftpwarden-restore)
- [Kubernetes Commands](#kubernetes-commands)
  - [sftpwarden kube](#sftpwarden-kube)
  - [sftpwarden helm](#sftpwarden-helm)
- [Provider Transfer Commands](#provider-transfer-commands)
  - [sftpwarden provider export](#sftpwarden-provider-export)
  - [sftpwarden provider import](#sftpwarden-provider-import)
  - [sftpwarden provider copy](#sftpwarden-provider-copy)
  - [sftpwarden provider schema](#sftpwarden-provider-schema)
  - [sftpwarden provider keys migrate](#sftpwarden-provider-keys-migrate)
- [User Commands](#user-commands)
  - [sftpwarden users](#sftpwarden-users)
  - [sftpwarden user show](#sftpwarden-user-show)
  - [sftpwarden user create](#sftpwarden-user-create)
  - [sftpwarden user update](#sftpwarden-user-update)
  - [sftpwarden user disable and enable](#sftpwarden-user-disable-and-enable)
  - [sftpwarden user remove](#sftpwarden-user-remove)
  - [sftpwarden user key](#sftpwarden-user-key)
- [Context Commands](#context-commands)
  - [sftpwarden context ls](#sftpwarden-context-ls)
  - [sftpwarden context current](#sftpwarden-context-current)
  - [sftpwarden context use](#sftpwarden-context-use)
  - [sftpwarden context show](#sftpwarden-context-show)
  - [sftpwarden context FIELD](#sftpwarden-context-field)
  - [sftpwarden context add](#sftpwarden-context-add)
  - [sftpwarden context rename](#sftpwarden-context-rename)
  - [sftpwarden context remove](#sftpwarden-context-remove)
  - [sftpwarden context clear](#sftpwarden-context-clear)
- [Config Commands](#config-commands)
  - [sftpwarden config PATH](#sftpwarden-config-path)
  - [sftpwarden config show](#sftpwarden-config-show)
  - [sftpwarden config default-provider](#sftpwarden-config-default-provider)
- [Watcher Commands](#watcher-commands)
  - [sftpwarden watcher status](#sftpwarden-watcher-status)
  - [sftpwarden watcher install](#sftpwarden-watcher-install)
  - [sftpwarden watcher uninstall](#sftpwarden-watcher-uninstall)
- [Runtime Commands](#runtime-commands)
  - [sftpwarden runtime health](#sftpwarden-runtime-health)

## How the CLI Thinks

SFTPWarden works with contexts. A context is a named environment, such as `dev`,
`prod`, or `archive`. It tells the CLI where the project lives and whether it is
local, remote, or remote-only.

The usual workflow is:

```bash
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
sftpwarden deploy
sftpwarden user create alice --password "correct horse battery staple"
sftpwarden refresh
```

After `sftpwarden init dev`, the `dev` context becomes active. That means most
commands can be run without `--context`.

To switch later:

```bash
sftpwarden context use dev
```

To run one command against a different context:

```bash
sftpwarden users --context prod
sftpwarden users -c prod
```

## Common Flags

| Flag | What it does | When to use it |
| --- | --- | --- |
| `--context`, `-c` | Selects one registered context for this command. | When you do not want to change the active context. |
| `--config` | Uses a specific `sftpwarden.yaml` file. | For scripts or one-off validation. |
| `--json` | Prints machine-readable JSON. | For automation and CI. |
| `--dry-run` | Shows what would happen without changing anything. | Before deploys or remote operations. |
| `--yes`, `-y` | Accepts confirmation prompts. | For CI or scripted workflows. |

## Shell Autocomplete

Typer adds shell completion helpers to the root `sftpwarden` command.

Install autocomplete for your current shell:

```bash
sftpwarden --install-completion
```

Then open a new terminal or reload your shell. Completion works for commands and
options, for example:

```bash
sftpwarden con<TAB>
sftpwarden user create --<TAB>
```

If you want to review or install the shell script manually, print it instead:

```bash
sftpwarden --show-completion
```

## Main Commands

### `sftpwarden init`

Creates a new SFTPWarden project and registers it as the active context.

For local projects, create a folder, enter it, and run init:

```bash
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
```

This creates:

```text
sftpwarden.yaml
users.yaml
docker-compose.yml
data/
state/
host_keys/
```

Use `--root` only when you want to initialize a folder without `cd`:

```bash
sftpwarden init dev --root ~/sftpwarden-dev --yes
```

Choose the user provider schema explicitly when you want to pin the format:

```bash
sftpwarden init demo --user-schema 1 --yes
sftpwarden init prod --user-schema 2 --yes
```

Schema v1 is the simple `public_keys` format. Schema v2 is the default for new
projects and enables named key lifecycle metadata.

Create a new remote local-sync project:

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden --critical
```

Create a remote-only context:

```bash
sftpwarden init archive \
  --remote deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical
```

Create a Kubernetes manifests project:

```bash
sftpwarden init prod --deploy kube --yes
```

Create a Helm project:

```bash
sftpwarden init prod --deploy helm --yes
```

For Kubernetes and Helm projects, `init` checks the configured namespace with
`kubectl`. If the namespace is missing, interactive init asks whether to create
it. The default namespace is `sftpwarden`, and `--yes` creates it automatically
when needed. Use `--namespace <name>` for a different existing or new namespace;
`--no-create-namespace` aborts when the selected namespace is missing.

Use `context add` instead of `init` only when the SFTPWarden project already exists
and you just want to register it on this machine.

### Database init flags

Use these when the users provider is MySQL, MariaDB, PostgreSQL, or MongoDB:

```bash
sftpwarden init prod \
  --provider mysql \
  --dsn 'mysql://sftpwarden:change-me@db.example.com:3306/sftpwarden' \
  --create-table
```

`--dsn` is a conventional database URL:

```text
mysql://user:password@host:3306/database
mariadb://user:password@host:3306/database
postgresql://user:password@host:5432/database
mongodb://user:password@host:27017/database
```

For production or shared environments, prefer an environment variable and pass
the reference:

```bash
export SFTPWARDEN_MYSQL_DSN='mysql://sftpwarden:change-me@db.example.com:3306/sftpwarden'

sftpwarden init prod \
  --provider mysql \
  --dsn '${SFTPWARDEN_MYSQL_DSN}' \
  --create-table
```

If `--dsn` is omitted during interactive MySQL, MariaDB, or PostgreSQL init,
SFTPWarden prompts for host, port, database, username, and password, then builds
the DSN. For MongoDB, interactive init asks for the MongoDB DSN directly.

Important flags:

| Flag | What it does |
| --- | --- |
| `--provider mysql` | Uses the MySQL provider. |
| `--provider mariadb` | Uses the MariaDB provider. |
| `--provider postgresql` | Uses the PostgreSQL provider. |
| `--provider mongodb` | Uses the MongoDB provider. |
| `--dsn` | Sets the database URL/DSN. Environment variable references are allowed. |
| `--query` | Sets a custom read-only user query. |
| `--table` | Sets the users table name. Default: `sftp_users`. |
| `--collection` | Sets the MongoDB collection name. Default: `sftp_users`. |
| `--deploy`, `-d` | Stores the deployment method: `compose`, `kube`, or `helm`. Default: `compose`. |
| `--namespace` | Sets the Kubernetes namespace for `kube` or `helm` projects. Default: `sftpwarden`. |
| `--create-namespace` | Creates a missing Kubernetes namespace during `init`. Implied by `--yes` when the namespace is missing. |
| `--no-create-namespace` | Aborts when the Kubernetes namespace is missing. |
| `--skip-checks` | Skips remote and Kubernetes prerequisite checks during `init`. Use this only when preparing files before the target system is reachable. |
| `--create-table` | Creates the SQL table or MongoDB collection/index if missing. |
| `--no-create-table` | Aborts if the SQL table or MongoDB collection is missing. |

If neither `--create-table` nor `--no-create-table` is passed, interactive `init`
asks whether to create the storage or abort.

### `sftpwarden deploy`

Starts or updates the SFTP runtime with the configured deployment target.

```bash
sftpwarden deploy
```

For Compose contexts, this runs Docker Compose in the context project folder.
Source checkouts build the local runtime image:

```bash
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps sftpwarden
```

Python package installations and custom `docker.image` values pull an image
instead:

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps sftpwarden
```

Before a local deploy, SFTPWarden checks that Docker Compose v2 is available. If
`docker compose version` fails, install Docker Compose and run `sftpwarden deploy`
again.

Preview without applying:

```bash
sftpwarden deploy --dry-run
sftpwarden deploy --dry-run --json
```

For Kubernetes manifests, deploy renders `kubernetes.yml` and applies it with
`kubectl`. For Helm mode, deploy writes `values.yaml` and runs
`helm upgrade --install`. Kubernetes and Helm deploys then restart the runtime
StatefulSet so PVC/config/probe changes such as `kubernetes.data_storage_size`
or `kubernetes.liveness_probe.period_seconds` are remounted or reloaded. For
YAML/CSV providers, this is also the step that copies the current local provider
file into the provider PVC.

Deploy a critical context without a prompt:

```bash
sftpwarden deploy --yes
```

### `sftpwarden validate`

Checks that a project config is valid and that the provider path can be resolved.

```bash
sftpwarden validate
sftpwarden validate --json
sftpwarden validate --config ~/sftpwarden-dev/sftpwarden.yaml
```

Use this after editing `sftpwarden.yaml` or before committing example configs.

### `sftpwarden compose`

Prints the Docker Compose file that SFTPWarden would generate.

```bash
sftpwarden compose
```

Write it to `docker-compose.yml`:

```bash
sftpwarden compose --write
```

Most users can run `sftpwarden deploy` instead. Use `compose` when you want to
inspect or regenerate the Compose file.

### `sftpwarden plan`

Shows what runtime user changes SFTPWarden would apply.

```bash
sftpwarden plan
sftpwarden plan --json
```

Use this before `refresh` when you want to preview user creation, updates, or
disabled users. The human-readable output explains whether user/provider changes
were detected and that those actions will be applied by `sftpwarden refresh`.
It also checks deploy-level configuration differences, such as a generated
`docker-compose.yml`, `kubernetes.yml`, or `values.yaml` that no longer matches
`sftpwarden.yaml`. Those changes are applied by `sftpwarden deploy`; `refresh`
only looks at users.

## Kubernetes Commands

### `sftpwarden kube`

Kubernetes manifest mode commands:

```bash
sftpwarden kube render
sftpwarden kube apply
sftpwarden kube status
sftpwarden kube logs
sftpwarden kube doctor
sftpwarden kube delete --yes
```

`render` does not require a cluster. `apply`, `status`, `logs`, `doctor`, and
`delete` use `kubectl`. Delete requires `--yes` unless you confirm interactively.
`apply` also restarts the runtime StatefulSet after the manifests are applied.
For YAML/CSV providers, `apply` copies the rendered local provider file into the
provider PVC during that rollout.

### `sftpwarden helm`

Helm mode commands:

```bash
sftpwarden helm values --write
sftpwarden helm template
sftpwarden helm lint
sftpwarden helm upgrade --install
sftpwarden helm uninstall --yes
```

`values` does not require Helm. Template, lint, upgrade, and uninstall require
`helm`. `upgrade` restarts the runtime StatefulSet after Helm succeeds. For
YAML/CSV providers, `upgrade` copies the rendered local provider file into the
provider PVC during that rollout.
Uninstall requires `--yes` unless you confirm interactively.

Source checkouts use the local `charts/sftpwarden` chart. Python package
installations use the published OCI chart
`oci://ghcr.io/kithuto/charts/sftpwarden` pinned to the installed CLI version.

The official chart also includes a Helm test hook that runs
`sftpwarden runtime health` against the installed release:

```bash
helm test <release> --namespace <namespace>
```

### `sftpwarden refresh`

Tells the running container to reload users immediately.

```bash
sftpwarden refresh
```

Refresh every registered context:

```bash
sftpwarden refresh --all
```

Preview the command without running it:

```bash
sftpwarden refresh --dry-run
```

Use `refresh` after changing users that are already visible to the runtime. For
Compose and remote Compose contexts this runs Docker Compose. For Kubernetes
contexts it runs `kubectl exec` against the runtime pod, which is useful for
database-backed providers such as PostgreSQL, MariaDB/MySQL, and MongoDB.
Kubernetes YAML/CSV provider changes require `deploy`, `kube apply`, or
`helm upgrade --install` because the local file must first be copied into the
provider PVC.

### `sftpwarden sync`

Shows the local user provider files that would be synchronized to remote
local-sync contexts.

```bash
sftpwarden sync
sftpwarden sync --json
sftpwarden sync --dry-run
```

This does not reload the runtime. It is about copying editable files to remote
hosts.

### `sftpwarden watch`

Keeps watching local YAML/CSV/SQLite user provider files and syncs user changes to
remote local-sync contexts.

```bash
sftpwarden watch
sftpwarden watch --interval 5
```

Use `watch` only for remote `local-sync` contexts. Remote-only contexts do not use
a watcher.

### `sftpwarden info`

Shows the resolved context.

```bash
sftpwarden info
sftpwarden info --json
```

Use this when you are unsure which context a command will use.

### `sftpwarden doctor`

Checks whether required local tools are available.

```bash
sftpwarden doctor
sftpwarden doctor --json
```

It checks tools such as `docker`, `ssh`, `rsync` or `scp`, `kubectl`, and `helm`.

### `sftpwarden health`

Checks the active project and, when available, the running runtime.

```bash
sftpwarden health
sftpwarden health --json
```

The report includes config validation, provider readability, Compose drift, and
runtime health. JSON output is useful for monitoring or CI.

### `sftpwarden backup`

Creates a `.tar.gz` backup of the operational project state.

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
sftpwarden backup --include-data --output full-backup.tar.gz
sftpwarden backup --dry-run --json
```

By default, backup includes config, Compose, `provider/users.json` with the
current users read from the provider, raw local provider files, host keys, and
runtime state. SQL and MongoDB providers are captured through that JSON user
snapshot when the CLI can reach the configured database. User data under `data/`
is included only with `--include-data`.

### `sftpwarden restore`

Restores a backup into the active or selected context.

```bash
sftpwarden restore sftpwarden-prod.tar.gz
sftpwarden restore sftpwarden-prod.tar.gz --yes
sftpwarden restore sftpwarden-prod.tar.gz --include-data
```

Restore creates a safety backup before overwriting files. Restoring data with
`--include-data` requires explicit confirmation unless `--yes` is used.

## Provider Transfer Commands

Provider transfer commands are for moving users between providers or contexts.
They require explicit `--merge` or `--replace` when writing to a destination.

### `sftpwarden provider export`

Exports users from a context/provider.

```bash
sftpwarden provider export --format json
sftpwarden provider export --output users.yaml
sftpwarden provider export --context prod --format csv --output users.csv
```

Without `--output`, export writes raw YAML/CSV/JSON to stdout without Rich
decoration so it can be redirected.

### `sftpwarden provider import`

Imports users into a context/provider.

```bash
sftpwarden provider import --input users.json --merge
sftpwarden provider import --input users.yaml --replace --dry-run
sftpwarden provider import --input users.csv --merge --json
```

`--merge` upserts imported users and keeps destination-only users. `--replace`
makes the destination exactly match the input file.

When the destination is a Kubernetes YAML/CSV project, import updates the local
provider file and reports that deploy is required. Database-backed Kubernetes
providers can be refreshed normally because the runtime reads the database
directly.

### `sftpwarden provider copy`

Copies users between two registered contexts.

```bash
sftpwarden provider copy \
  --from-context dev \
  --to-context prod \
  --merge \
  --dry-run
```

Copy supports all readable/mutable providers. Comment-only changes do not refresh
the runtime because they do not affect runtime user state.

When the destination is a Kubernetes YAML/CSV project, copy updates the local
provider file and reports that deploy is required. Database-backed Kubernetes
providers can be refreshed normally because the runtime reads the database
directly.

### `sftpwarden provider schema`

Inspects and migrates the user schema used by the selected provider.

```bash
sftpwarden provider schema show
sftpwarden provider schema migrate --to 2 --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

Migrations never happen during ordinary reads. A write migration creates a
logical YAML backup by default unless `--no-backup` is used.

### `sftpwarden provider keys migrate`

Shortcut for migrating anonymous `public_keys` users to schema v2 named keys.

```bash
sftpwarden provider keys migrate --dry-run
sftpwarden provider keys migrate --backup --yes
```

## User Commands

### `sftpwarden users`

Lists users in the current provider.

```bash
sftpwarden users
sftpwarden users --json
```

### `sftpwarden user show`

Prints one user as JSON.

```bash
sftpwarden user show alice
```

### `sftpwarden user create`

Adds a user to the provider.

Password user:

```bash
sftpwarden user create alice --password "correct horse battery staple"
```

Public key user:

```bash
sftpwarden user create alice --public-key "ssh-ed25519 AAAA..."
```

With metadata:

```bash
sftpwarden user create alice \
  --password "correct horse battery staple" \
  --comment "Finance inbox" \
  --upload-dir inbound
```

User mutations refresh the runtime automatically unless `--no-refresh` is used.
For Kubernetes YAML/CSV providers, the CLI saves the local provider file and
prints the deploy/apply/upgrade command to run because the provider PVC is synced
during rollout. For Kubernetes database providers, refresh targets the runtime
pod with `kubectl exec`.

### `sftpwarden user update`

Changes an existing user.

```bash
sftpwarden user update alice --comment "Finance inbox"
sftpwarden user update alice --upload-dir inbound
sftpwarden user update alice --uid 12001 --gid 12001
```

Updating only `comment` does not refresh the runtime because comments are metadata.

### `sftpwarden user disable` and `enable`

Disables or enables an entire user.

```bash
sftpwarden user disable alice
sftpwarden user enable alice
```

### `sftpwarden user remove`

Removes a user from the provider. By default, user files are kept.

```bash
sftpwarden user remove alice --yes
```

Permanently delete the user's data directory too:

```bash
sftpwarden user remove alice --delete-files --yes
```

`--delete-files` also accepts `--force-delete-files` as an explicit alias. Use it
carefully; file deletion is irreversible.

### `sftpwarden user key`

Manages SSH keys for one user.

```bash
sftpwarden user key list alice
sftpwarden user key show alice prod-ci
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub
sftpwarden user key remove alice prod-ci --yes
sftpwarden user key disable alice prod-ci
sftpwarden user key enable alice prod-ci
sftpwarden user key rename alice old-name new-name
sftpwarden user key rotate alice prod-ci --public-key ./prod-ci-new.pub
sftpwarden user key expire alice prod-ci --at 2027-01-01
sftpwarden user key import alice --from-dir ./keys
```

Schema v1 providers can list, show, add, and remove anonymous keys using
deterministic names and fingerprints. Operations that require persisted key
metadata migrate to schema v2 after confirmation. In non-interactive use, pass
`--yes`; with `--dry-run`, SFTPWarden shows the migration and key operation
without writing changes. During `key import --from-dir`, each key name defaults
to the `.pub` file name without the extension unless `--name` is provided for a
single-file import.

## Context Commands

### `sftpwarden context ls`

Lists registered contexts.

```bash
sftpwarden context ls
sftpwarden context ls --json
```

### `sftpwarden context current`

Prints the active context name.

```bash
sftpwarden context current
```

### `sftpwarden context use`

Sets the active context.

```bash
sftpwarden context use dev
```

`context default dev` does the same thing.

### `sftpwarden context show`

Shows one context as JSON.

```bash
sftpwarden context show dev
```

If no name is given, it shows the active context:

```bash
sftpwarden context show
```

### `sftpwarden context FIELD`

Reads or updates one field in the active context registry entry. If you do not
pass a value, SFTPWarden prints the current value.

```bash
sftpwarden context name
sftpwarden context root
sftpwarden context type
sftpwarden context remote-root
```

Pass a value to update the field:

```bash
sftpwarden context name dev2
sftpwarden context provider csv
sftpwarden context remote-root /opt/sftpwarden-prod --yes
```

Renaming a context with `sftpwarden context name dev2` also updates
`project.name` in the local `sftpwarden.yaml` when the context has a local config.

Changing `root` copies the project files to the new folder and updates the
registered `config` path:

```bash
sftpwarden context root ~/sftpwarden-dev2 --yes
sftpwarden context root ~/sftpwarden-dev2 --yes --delete-old-root
```

Without `--yes`, SFTPWarden asks before copying. `--delete-old-root` removes the
old folder after the copy.

Convert a local context to a remote local-sync context:

```bash
sftpwarden context type remote \
  --remote deploy@example.com:/opt/sftpwarden \
  --yes
```

Convert a remote context back to local:

```bash
sftpwarden context type local --yes
```

When converting remote to local, SFTPWarden removes remote metadata. Without
`--yes`, it asks for confirmation.

### `sftpwarden context add`

Registers an existing context. Use this when the project already exists and you
want this machine to know about it.

Register an existing remote project:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
```

Register an existing local project:

```bash
cd ~/sftpwarden-dev
sftpwarden context add dev
```

For new projects, prefer `sftpwarden init`.

### `sftpwarden context rename`

Renames a registered context.

```bash
sftpwarden context rename old-name new-name
```

### `sftpwarden context remove`

Removes a context from the local registry. It does not delete remote files or local
project files.

```bash
sftpwarden context remove old-name --yes
```

### `sftpwarden context clear`

Clears the active context.

```bash
sftpwarden context clear
```

## Config Commands

### `sftpwarden config PATH`

Reads or updates one value in the active project's `sftpwarden.yaml`. Use dotted
paths that match the YAML structure.

Read values:

```bash
sftpwarden config project.name
sftpwarden config server.port
sftpwarden config provider.type
```

Update values:

```bash
sftpwarden config project.name dev2
sftpwarden config server.port 2200
sftpwarden config sync.interval_seconds 30
sftpwarden config auth.allow_password false
sftpwarden config healthcheck.interval_seconds 45
sftpwarden config kubernetes.liveness_probe.period_seconds 45
```

Values are parsed with YAML scalar rules, so numbers become numbers, `true` and
`false` become booleans, and `null` clears nullable fields.

Changing `project.name` also renames the registered context when the command is
using a registered context.

### `sftpwarden config show`

Shows global CLI settings stored under `~/.sftpwarden/config.toml`.

```bash
sftpwarden config show
sftpwarden config show --json
```

### `sftpwarden config default-provider`

Shows or changes the provider used by default when you run `init`.

```bash
sftpwarden config default-provider
sftpwarden config default-provider yaml
sftpwarden config default-provider csv
```

## Watcher Commands

The watcher is only useful for remote `local-sync` contexts. It syncs YAML/CSV/SQLite
user provider files to remote hosts. It does not sync `sftpwarden.yaml`; config
changes require `sftpwarden deploy`.

### `sftpwarden watcher status`

Shows whether a watcher is installed and which files it would watch.

```bash
sftpwarden watcher status
sftpwarden watcher status --json
```

### `sftpwarden watcher install`

Installs the watcher.

Auto-detected watcher:

```bash
sftpwarden watcher install
sftpwarden watcher install --watcher auto
```

Native watcher backends:

```bash
sftpwarden watcher install --watcher systemd
sftpwarden watcher install --watcher openrc
sftpwarden watcher install --watcher runit
sftpwarden watcher install --watcher supervisord
sftpwarden watcher install --watcher launchd
sftpwarden watcher install --watcher windows-task
```

Docker watcher:

```bash
sftpwarden watcher install --watcher docker
sftpwarden watcher install --watcher docker --image registry.example.com/watcher:tag
```

Dry-run:

```bash
sftpwarden watcher install --dry-run
```

By default, install writes the backend file and activates the scheduler. Use
`--no-activate` to render the file without starting or enabling it. Linux native
backends such as systemd, OpenRC, runit, and supervisord write into system
service directories during activation, so those commands use `sudo` and may ask
for the host user's sudo password.

`auto` detects Windows Task Scheduler on Windows, launchd on macOS, and systemd,
OpenRC, runit, or supervisord on Linux. If no native scheduler is found,
interactive installs ask whether to use Docker. `--yes` accepts Docker fallback
automatically; non-interactive workflows should pass `--watcher docker` when
Docker is intended.

Native modes run on the host and use the host SSH config, agent, known hosts, and
default identity. Docker mode requires explicit dedicated SSH keys for remote
contexts. It writes a Docker-specific context registry with container paths,
mounts local project folders read-only, and copies mounted keys inside the
container with private permissions. Source checkouts build
`sftpwarden-watcher:local`; Python package installations use
`ghcr.io/kithuto/sftpwarden-watcher:<installed-version>` unless `--image`
overrides it.

If another watcher backend is already installed, installing a different backend
requires confirmation or `--yes`. SFTPWarden deactivates the old backend before
writing and activating the new one.

### `sftpwarden watcher uninstall`

Removes the watcher.

```bash
sftpwarden watcher uninstall --yes
```

Uninstall deactivates the backend-specific scheduler entry, removes the generated
watcher file, and clears SFTPWarden's watcher metadata.

## Runtime Commands

Runtime commands are intended to run inside the SFTPWarden container. Most users do
not need to call them directly.

```bash
sftpwarden runtime plan --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime refresh --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime sync --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime health --config /etc/sftpwarden/sftpwarden.yaml
```

They are used by the container to apply provider data to Linux users and OpenSSH.

### `sftpwarden runtime health`

Checks runtime-internal requirements without changing state.

```bash
sftpwarden runtime health --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime health --json
```

The generated Docker Compose file uses this command as the container healthcheck.
Tune the Compose timings with `healthcheck.interval_seconds`,
`healthcheck.timeout_seconds`, `healthcheck.retries`, and
`healthcheck.start_period_seconds`. Kubernetes manifests and generated Helm values
use `kubernetes.startup_probe.*`, `kubernetes.readiness_probe.*`, and
`kubernetes.liveness_probe.*`. The command exits `0` when critical runtime checks
pass and `1` when they fail.

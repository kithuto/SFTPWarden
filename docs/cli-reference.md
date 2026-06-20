# CLI Reference

This page explains the `sftpwarden` command line for people who are new to the
project. You do not need to know SFTPWarden internals to use these commands.

## Table of Contents

- [How the CLI Thinks](#how-the-cli-thinks)
- [Common Flags](#common-flags)
- [Shell Autocomplete](#shell-autocomplete)
- [Main Commands](#main-commands)
  - [sftpwarden init](#sftpwarden-init)
  - [SQL init flags](#sql-init-flags)
  - [sftpwarden deploy](#sftpwarden-deploy)
  - [sftpwarden validate](#sftpwarden-validate)
  - [sftpwarden compose](#sftpwarden-compose)
  - [sftpwarden plan](#sftpwarden-plan)
  - [sftpwarden refresh](#sftpwarden-refresh)
  - [sftpwarden sync](#sftpwarden-sync)
  - [sftpwarden watch](#sftpwarden-watch)
  - [sftpwarden info](#sftpwarden-info)
  - [sftpwarden doctor](#sftpwarden-doctor)
- [User Commands](#user-commands)
  - [sftpwarden users](#sftpwarden-users)
  - [sftpwarden user show](#sftpwarden-user-show)
  - [sftpwarden user add](#sftpwarden-user-add)
  - [sftpwarden user update](#sftpwarden-user-update)
  - [sftpwarden user remove](#sftpwarden-user-remove)
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
sftpwarden user add alice --password "correct horse battery staple"
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
sftpwarden user add --<TAB>
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

Use `context add` instead of `init` only when the SFTPWarden project already exists
and you just want to register it on this machine.

### SQL init flags

Use these when the users provider is MySQL or PostgreSQL:

```bash
sftpwarden init prod \
  --provider mysql \
  --dsn '${SFTPWARDEN_MYSQL_DSN}' \
  --create-table
```

Important flags:

| Flag | What it does |
| --- | --- |
| `--provider mysql` | Uses the MySQL provider. |
| `--provider postgresql` | Uses the PostgreSQL provider. |
| `--dsn` | Sets the SQL connection string. Environment variable references are allowed. |
| `--query` | Sets a custom read-only user query. |
| `--table` | Sets the users table name. Default: `sftp_users`. |
| `--create-table` | Creates the SQL table if it is missing. |
| `--no-create-table` | Aborts if the SQL table is missing. |

If neither `--create-table` nor `--no-create-table` is passed, interactive `init`
asks whether to create the table or abort.

### `sftpwarden deploy`

Starts or updates the SFTP runtime with Docker Compose.

```bash
sftpwarden deploy
```

For local contexts, this runs Docker Compose in the context project folder:

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps sftpwarden
```

Before a local deploy, SFTPWarden checks that Docker Compose v2 is available. If
`docker compose version` fails, install Docker Compose and run `sftpwarden deploy`
again.

Preview without applying:

```bash
sftpwarden deploy --dry-run
```

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
`docker-compose.yml` that no longer matches `sftpwarden.yaml`. Those changes are
applied by `sftpwarden deploy`; `refresh` only looks at users.

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

Use `refresh` after changing users. Use `deploy` after changing configuration,
Docker Compose, or starting the runtime.

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

Keeps watching local YAML/CSV user provider files and syncs user changes to
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

It checks tools such as `docker`, `ssh`, and `rsync`.

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

### `sftpwarden user add`

Adds a user to the provider.

Password user:

```bash
sftpwarden user add alice --password "correct horse battery staple"
```

Public key user:

```bash
sftpwarden user add alice --public-key "ssh-ed25519 AAAA..."
```

With metadata:

```bash
sftpwarden user add alice \
  --password "correct horse battery staple" \
  --comment "Finance inbox" \
  --upload-dir inbound
```

User mutations refresh the runtime automatically unless `--no-refresh` is used.

### `sftpwarden user update`

Changes an existing user.

```bash
sftpwarden user update alice --comment "Finance inbox"
sftpwarden user update alice --upload-dir inbound
sftpwarden user update alice --uid 12001 --gid 12001
sftpwarden user update alice --disabled
sftpwarden user update alice --enabled
```

Updating only `comment` does not refresh the runtime because comments are metadata.

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

The watcher is only useful for remote `local-sync` contexts. It syncs YAML/CSV
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

Systemd watcher:

```bash
sftpwarden watcher install --watcher systemd
```

Docker watcher:

```bash
sftpwarden watcher install --watcher docker
```

Dry-run:

```bash
sftpwarden watcher install --watcher systemd --dry-run
```

Systemd mode uses `sudo` and enables the service so it starts after reboot.
Docker mode requires explicit dedicated SSH keys for remote contexts.

### `sftpwarden watcher uninstall`

Removes the watcher.

```bash
sftpwarden watcher uninstall --yes
```

## Runtime Commands

Runtime commands are intended to run inside the SFTPWarden container. Most users do
not need to call them directly.

```bash
sftpwarden runtime plan --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime refresh --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime sync --config /etc/sftpwarden/sftpwarden.yaml
```

They are used by the container to apply provider data to Linux users and OpenSSH.

# Context Commands

A context is a named environment registered on the operator machine. It tells
SFTPWarden where the project lives, which provider it uses, whether it is local
or remote, and how remote sync should work.

## `sftpwarden context`

Command group for the context registry. It also exposes dynamic field commands
such as `sftpwarden context root` and `sftpwarden context remote-root`.

```bash
sftpwarden context --help
```

Subcommands:

- `ls`
- `current`
- `use`
- `default`
- `show`
- `add`
- `rename`
- `remove`
- `clear`
- dynamic field commands documented under [`sftpwarden context FIELD`](#sftpwarden-context-field)

## `sftpwarden context ls`

Lists registered contexts.

```bash
sftpwarden context ls
sftpwarden context ls --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--json` | flag | false | Prints the full context registry as JSON. |

### Cleanup Behavior

Before listing, SFTPWarden prunes local contexts whose local project folder was
deleted manually. For remote local-sync contexts, this cleanup is local-only: it
removes stale local registry and watcher traces but does not connect to the
remote host or delete remote files.

Remote-only contexts have no local project folder to prune. Real remote commands
such as `deploy`, `refresh`, `health`, or `backup` detect missing remote roots
when they connect.

## `sftpwarden context current`

Prints the active context name.

```bash
sftpwarden context current
```

### Options

No command-specific options.

## `sftpwarden context use`

Sets the active context.

```bash
sftpwarden context use dev
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `name` | Yes | `TEXT` | Existing registered context to make active. |

### Options

No command-specific options.

## `sftpwarden context default`

Explicit form of `sftpwarden context use`.

```bash
sftpwarden context default prod
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `name` | Yes | `TEXT` | Existing registered context to make active. |

### Options

No command-specific options.

## `sftpwarden context show`

Shows one context as JSON.

```bash
sftpwarden context show
sftpwarden context show prod
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `name` | No | `TEXT` | Context to show. If omitted, the active context is shown. |

### Options

No command-specific options.

## `sftpwarden context add`

Registers an existing local or remote project.

```bash
sftpwarden context add dev
sftpwarden context add dev --root ~/sftpwarden-dev
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
sftpwarden context add archive deploy@example.com:/opt/sftpwarden --remote-only --critical
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `name` | Yes | `TEXT` | Context name to register. |
| `remote_url` | No | `REMOTE` | Optional `user@host:/path` remote URL. If omitted, SFTPWarden registers a local project. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--root` | `PATH` | `.` | Local project root for local and remote local-sync contexts. |
| `--provider` | `yaml`, `csv`, `sqlite`, `mysql`, `mariadb`, `postgresql`, `mongodb` | provider from local config, or global default for remote | Provider type stored in the registry. |
| `--user` | `TEXT` | parsed from remote URL or SSH default | Remote SSH user. |
| `--port` | `INTEGER` | global SSH default | Remote SSH port. |
| `--remote-root` | `PATH` | global remote root | Explicit remote project root. |
| `--remote-only` | flag | false | Registers a context that has no local project files. |
| `--ssh-key` | `PATH` | SSH default | Explicit SSH key for remote operations and Docker watcher mode. |
| `--watcher` | `auto`, `systemd`, `openrc`, `runit`, `supervisord`, `launchd`, `windows-task`, `docker` | auto when needed | Watcher backend to install or reuse for remote local-sync contexts. |
| `--critical` | flag | false | Marks the context as critical. |
| `--skip-checks` | flag | false | Skips remote prerequisite checks. |
| `--yes`, `-y` | flag | false | Accepts prompts, including production-like non-critical confirmation and watcher prompts. |

### When To Use It

Use `context add` when the SFTPWarden project already exists. Use
`sftpwarden init` for new projects.

## `sftpwarden context rename`

Renames a registered context.

```bash
sftpwarden context rename old-name new-name
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `old_name` | Yes | `TEXT` | Existing registered context. |
| `new_name` | Yes | `TEXT` | Replacement context name. |

### Options

No command-specific options.

## `sftpwarden context remove`

Removes a context and cleans project-owned local resources.

```bash
sftpwarden context remove dev
sftpwarden context remove dev --yes
sftpwarden context remove prod --yes --delete-remote
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `name` | Yes | `TEXT` | Registered context to remove. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--yes`, `-y` | flag | false | Accepts local cleanup confirmation. For remote contexts, `--yes` keeps remote files unless `--delete-remote` is also used. |
| `--delete-remote` | flag | false | Also stops/removes remote runtime resources and deletes the remote project root for remote contexts. |

### Effects

For local Compose contexts, SFTPWarden stops the local Compose runtime when
possible and removes project-owned local files when they are not shared with
another context. For remote local-sync contexts, it removes the local synced
project folder and watcher traces. Remote files are left untouched unless
`--delete-remote` is explicitly requested or confirmed interactively.

## `sftpwarden context clear`

Clears the active/default context.

```bash
sftpwarden context clear
```

### Options

No command-specific options.

## `sftpwarden context FIELD`

Reads or updates one field in the active context registry entry. If `VALUE` is
omitted, the command prints the current value. If `VALUE` is supplied, the
command updates the registry field.

```bash
sftpwarden context root
sftpwarden context root ~/sftpwarden-dev2 --yes
sftpwarden context remote-root /opt/sftpwarden-prod --yes
sftpwarden context type remote --remote deploy@example.com:/opt/sftpwarden --yes
sftpwarden context type local --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `FIELD` | Yes | one supported field command | Registry field command, such as `root`, `type`, or `remote-root`. |
| `VALUE` | No | YAML-like scalar text | Replacement value. Omit it to read the current value. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Context entry to read or edit. |
| `--remote` | `REMOTE` | prompt when converting to remote | Remote URL used when changing `type` to `remote`. |
| `--root` | `PATH` | existing root or generated default | Local root used by root moves or type conversion. |
| `--user` | `TEXT` | existing remote user or SSH default | Remote SSH user used by type conversion. |
| `--port` | `INTEGER` | existing or default SSH port | Remote SSH port used by type conversion. |
| `--remote-root` | `PATH` | existing or default remote root | Remote root used by type conversion. |
| `--remote-only` | flag | false | Creates a remote-only registry entry when converting to remote. |
| `--delete-old-root` | flag | false | Deletes the previous local root after copying it during `root` migration. |
| `--yes`, `-y` | flag | false | Accepts prompts for root copy/delete, remote-root updates, type conversion, and watcher cleanup. |

### Supported Field Commands

| Command | Registry field | Notes |
| --- | --- | --- |
| `name` | `name` | Renames the context and updates `project.name` in local `sftpwarden.yaml` when present. |
| `type` | `type` | Accepts `local` or `remote`. Type conversion may add or remove remote metadata. |
| `root` | `root` | Copies project files to the new root and updates the registered config path. |
| `config` | `config` | Direct registry config path. Use carefully. |
| `provider` | `provider` | Provider type stored in the registry. |
| `critical` | `critical` | Boolean critical-context flag. |
| `storage` | `storage` | Remote storage mode, normally `local-sync` or `remote-only` depending context type. |
| `watcher-required`, `watcher_required` | `watcher_required` | Boolean flag used by watcher planning. |
| `remote-root`, `remote_root`, `remote.remote_root` | `remote.remote_root` | Remote project root. Updates dependent remote config path. |
| `remote-config`, `remote_config`, `remote.remote_config` | `remote.remote_config` | Remote path to `sftpwarden.yaml`. |
| `ssh-key`, `ssh_key`, `remote.ssh_key` | `remote.ssh_key` | Explicit remote SSH key path. |
| `host`, `remote.host` | `remote.host` | Remote SSH host. |
| `user`, `remote.user` | `remote.user` | Remote SSH user. |
| `port`, `remote.port` | `remote.port` | Remote SSH port. |
| `compose-file`, `compose_file`, `remote.compose_file` | `remote.compose_file` | Remote Compose file path/name. |

### Safety

Changing context registry fields changes how future commands find and operate on
projects. It does not move remote files automatically. After changing fields that
affect deployment, run `sftpwarden deploy` to apply project desired state.

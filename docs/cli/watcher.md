# Watcher And Sync Commands

The watcher exists for remote `local-sync` contexts. It syncs editable user
provider files from the local project to the remote project. It does not sync
`sftpwarden.yaml`, and it does not apply deployment-level config changes. Use
`sftpwarden deploy` for config changes.

Editable provider files are derived by code from the provider type and project
config. The watcher targets YAML, CSV, and SQLite providers when the provider
file exists locally.

## `sftpwarden sync`

Lists or dry-runs the provider files that would be synced for remote local-sync
contexts.

```bash
sftpwarden sync
sftpwarden sync --dry-run
sftpwarden sync --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--dry-run` | flag | false | Prints planned sync targets without copying files. |
| `--json` | flag | false | Prints sync target data as JSON. |

### Effects

This command does not refresh the runtime and does not copy files by itself. It
reports the provider files that are eligible for remote local-sync workflows.

## `sftpwarden watch`

Runs a foreground polling loop. When a watched provider file changes, SFTPWarden
syncs it to the corresponding remote local-sync context.

```bash
sftpwarden watch
sftpwarden watch --interval 5
sftpwarden watch --dry-run
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--interval` | integer `>= 1` | `2` | Polling interval in seconds. |
| `--dry-run` | flag | false | Reports detected sync commands without copying files. |

### Effects

This command keeps running until stopped. It uses `scp` on Windows and `rsync`
elsewhere, according to the implementation.

## `sftpwarden watcher`

Command group for installing, inspecting, and uninstalling a local watcher
backend.

```bash
sftpwarden watcher --help
```

Subcommands:

- `status`
- `install`
- `uninstall`

## `sftpwarden watcher status`

Shows watcher installation state and derived sync targets.

```bash
sftpwarden watcher status
sftpwarden watcher status --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--json` | flag | false | Prints installed state, mode, path, activation state, and targets as JSON. |

### Effects

Read-only.

## `sftpwarden watcher install`

Installs or updates the local watcher backend.

```bash
sftpwarden watcher install
sftpwarden watcher install --watcher auto
sftpwarden watcher install --watcher systemd
sftpwarden watcher install --watcher docker
sftpwarden watcher install --watcher docker --image registry.example.com/watcher:tag
sftpwarden watcher install --no-activate
sftpwarden watcher install --dry-run
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--watcher`, `--mode` | `auto`, `systemd`, `openrc`, `runit`, `supervisord`, `launchd`, `windows-task`, or `docker` | configured default, normally `auto` | Watcher backend to install. `--mode` is an alias. |
| `--image` | Docker image reference | none | Docker watcher image override. Valid only when the resolved watcher mode is `docker`. |
| `--activate` / `--no-activate` | flag pair | `--activate` in the CLI command | Controls whether SFTPWarden starts/enables the watcher after writing files. |
| `--yes`, `-y` | flag | false | Accepts replacement prompts and allows Docker fallback when `auto` finds no native scheduler. |
| `--dry-run` | flag | false | Prints the install plan without writing files or activating a scheduler. |

### Effects

A real install writes backend-specific watcher files and records watcher metadata
in global CLI config. With activation enabled, it runs the backend activation
commands. If a different watcher is already installed, replacement requires
confirmation or `--yes`.

`auto` detects supported native schedulers first. If none is available, Docker
fallback requires explicit confirmation or `--yes`.

## `sftpwarden watcher uninstall`

Uninstalls the local watcher backend and clears watcher metadata.

```bash
sftpwarden watcher uninstall
sftpwarden watcher uninstall --yes
sftpwarden watcher uninstall --dry-run
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--yes`, `-y` | flag | false | Accepts the uninstall confirmation prompt. |
| `--dry-run` | flag | false | Prints the uninstall plan without deactivating or removing files. |

### Effects

A real uninstall deactivates the scheduler if the watcher was activated, removes
the generated watcher file, and clears watcher metadata.

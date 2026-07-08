# Core Commands

## `sftpwarden`

Root command for the SFTPWarden CLI.

```bash
sftpwarden --help
sftpwarden --version
sftpwarden version
```

### Root Options

| Flag | Value | What it does |
| --- | --- | --- |
| `--version` | flag | Prints the installed SFTPWarden version and exits. |
| `--install-completion` | flag | Installs shell completion for the current shell. |
| `--show-completion` | flag | Prints the shell completion script so you can inspect or install it manually. |
| `--help` | flag | Shows root command help. |

## `sftpwarden version`

Prints the installed SFTPWarden version and exits. This is the command form of
the root `--version` option.

```bash
sftpwarden version
sftpwarden --version
```

### Effects

Read-only. It does not read a project config, use a context, or contact any
runtime infrastructure.

## `sftpwarden deploy`

Applies deployment-level desired state for the selected context. This is the
normal command for applying changes from `sftpwarden.yaml` after editing it
manually or with `sftpwarden config`.

For Compose contexts, it runs Docker Compose. For Kubernetes manifest projects,
it renders and applies manifests. For Helm projects, it writes values and runs
Helm upgrade/install through the deployment service.

```bash
sftpwarden deploy
sftpwarden deploy --context prod
sftpwarden deploy --dry-run
sftpwarden deploy --dry-run --json
sftpwarden deploy --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--dry-run` | flag | false | Prints the deployment plan and pending provider schema reconciliation without applying changes. |
| `--json` | flag | false | Prints deployment and schema plan data as JSON. Most useful with `--dry-run`. |
| `--yes`, `-y` | flag | false | Accepts critical-context confirmation and pending forward provider schema migration prompts. |

### Effects

- May write generated deployment files such as `docker-compose.yml`,
  `kubernetes.yml`, or `values.yaml`.
- May migrate provider users forward when `provider.user_schema` requests a
  newer schema than the stored provider data.
- May start, update, or restart runtime infrastructure.
- For Kubernetes YAML/CSV providers, syncs the local provider file into the
  provider PVC as part of rollout.

## `sftpwarden validate`

Validates one project configuration file and resolves the provider path.

```bash
sftpwarden validate
sftpwarden validate --config ./sftpwarden.yaml
sftpwarden validate --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `sftpwarden.yaml` | Config file to validate. |
| `--json` | flag | false | Prints validation result, project name, provider type, config path, and provider path as JSON. |

### Effects

This command does not write files or contact the runtime. Use it after editing
`sftpwarden.yaml` and before committing examples.

## `sftpwarden compose`

Renders the Docker Compose file for a project.

```bash
sftpwarden compose
sftpwarden compose --config ./sftpwarden.yaml
sftpwarden compose --write
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `sftpwarden.yaml` | Config file used to render Compose. |
| `--write` | flag | false | Writes the generated Compose text to the configured compose file, normally `docker-compose.yml`. Without it, Compose text is printed. |

### Effects

Without `--write`, this command only prints. With `--write`, it updates the
Compose file but does not start containers. Run `sftpwarden deploy` to apply the
change.

## `sftpwarden plan`

Shows the runtime user plan for a local context. It also reports deployment-level
configuration drift so operators know whether `refresh` or `deploy` is the next
right command.

```bash
sftpwarden plan
sftpwarden plan --context prod
sftpwarden plan --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Resolves a project directly from a config path. |
| `--json` | flag | false | Prints runtime plan and deploy drift data as JSON. |

### Effects

This command is read-only. It compares provider users to runtime state and tells
you what `sftpwarden refresh` would apply.

## `sftpwarden refresh`

Tells the running runtime to reload users now.

```bash
sftpwarden refresh
sftpwarden refresh --context prod
sftpwarden refresh --all
sftpwarden refresh --dry-run
sftpwarden refresh --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Refreshes one registered context. |
| `--config` | `PATH` | none | Resolves a context directly from a config path. |
| `--all` | flag | false | Refreshes all registered contexts. Cannot be combined with a single-context intent. |
| `--dry-run` | flag | false | Prints the refresh command that would run without executing it. |
| `--json` | flag | false | Prints refresh target results as JSON. |

### Effects

`refresh` applies user/provider changes that are already visible to the runtime.
It does not apply `sftpwarden.yaml` deployment changes. For Kubernetes YAML/CSV
providers, run `sftpwarden deploy`, `sftpwarden kube apply`, or `sftpwarden helm
upgrade` because the local provider file must be copied into the provider PVC.

## `sftpwarden info`

Shows the resolved context entry.

```bash
sftpwarden info
sftpwarden info --context prod
sftpwarden info --config ./sftpwarden.yaml
sftpwarden info --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Shows a registered context. |
| `--config` | `PATH` | none | Resolves and shows the context for a config path. |
| `--json` | flag | false | Prints the context model as JSON. |

### Effects

Read-only. Use it when you are not sure which context a command will use.

## `sftpwarden doctor`

Checks whether local external tools are available.

```bash
sftpwarden doctor
sftpwarden doctor --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--json` | flag | false | Prints tool availability as JSON. |

### Checks

It checks for tools used by deployment and remote workflows: `docker`, `ssh`,
`rsync` or `scp`, `kubectl`, and `helm`.

## `sftpwarden health`

Checks project and runtime health for a context.

```bash
sftpwarden health
sftpwarden health --context prod
sftpwarden health --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--json` | flag | false | Prints health checks as JSON and exits `0` for healthy, `1` for unhealthy. |

### Effects

Health is read-only. It validates config, provider readability, generated
deployment drift, provider schema drift, runtime health when available, and
remote availability for remote-only contexts.

If a remote-only context points at a remote root that was manually deleted,
SFTPWarden removes only the stale local registry entry. If the remote server does
not respond, the context is kept and the command reports the SSH connectivity
failure clearly.

## `sftpwarden backup`

Creates a `.tar.gz` backup of operational project state.

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
sftpwarden backup --include-data --output full-backup.tar.gz
sftpwarden backup --dry-run --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--output`, `-o` | `PATH` | timestamped archive name | Archive path to create. |
| `--include-data` | flag | false | Includes SFTP user data under `data/`. Without it, operational state is backed up but user files are excluded. |
| `--dry-run` | flag | false | Prints backup entries without writing an archive. |
| `--json` | flag | false | Prints backup result and entries as JSON. |
| `--yes`, `-y` | flag | false | Accepts the `--include-data` confirmation prompt. |

### Effects

Backups may include config, generated deployment files, host keys, runtime state,
raw local provider files, and a normalized provider user snapshot. Treat backups
as sensitive.

## `sftpwarden restore`

Restores a SFTPWarden backup into the selected context.

```bash
sftpwarden restore sftpwarden-prod.tar.gz
sftpwarden restore sftpwarden-prod.tar.gz --include-data --yes
sftpwarden restore sftpwarden-prod.tar.gz --dry-run --json
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `backup_path` | Yes | `PATH` | Backup archive to restore. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Destination context. |
| `--include-data` | flag | false | Restores SFTP user data from the archive. |
| `--dry-run` | flag | false | Validates and reports restore entries without overwriting files. |
| `--json` | flag | false | Prints restore result, entries, and safety backup path as JSON. |
| `--yes`, `-y` | flag | false | Accepts restore confirmation prompts. |

### Effects

A real restore creates a safety backup before overwriting files. Restoring user
data requires explicit confirmation unless `--yes` is used.

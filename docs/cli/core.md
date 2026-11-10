# Core Commands

These commands deploy an installation, inspect it or preserve it for recovery.
Choose the task below and consult the option table for its target selection.
Examples within each command are alternatives, not a single workflow.

| Task | Command |
| --- | --- |
| Apply saved configuration | `deploy` |
| Check configuration syntax and paths | `validate` |
| Preview desired changes | `plan` |
| Apply provider users | `refresh` |
| Check the runtime | `health` |
| Capture or recover operational state | `backup` / `restore` |

Use [Operations](../operations.md) and [Recovery](../recovery.md) for ordered
procedures.

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

Read-only. It does not read a project config, use a context or contact any
runtime infrastructure.

## `sftpwarden deploy`

Deploy inspects the actual runtime version and automatically prepares supported
upgrades, including required provider-layout and configured schema migrations.
It recommends a verified backup and combines upgrade, critical-context and schema
consent into one confirmation. `--yes` accepts that confirmation, not failed
compatibility or integrity checks. Official images advance to the CLI release;
custom images are preserved and their applied version is reported separately.
An installed watcher is reconciled after successful runtime activation. Required
watcher failures leave the operation incomplete. See [Upgrade](upgrade.md).

Kubernetes operations pin the selected cluster for inspection and activation;
deploy saves that explicit cluster selection in the project configuration.

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
  `kubernetes.yml` or `values.yaml`.
- May migrate provider users forward when `provider.user_schema` requests a
  newer schema than the stored provider data.
- May start, update or restart runtime infrastructure.
- Local and remote `local-sync` Compose deploys migrate file providers to a
  dedicated `provider/` directory, preserve recovery material, recreate the
  runtime and wait for readiness. Update external editor paths after migration;
  see [provider migration](../providers.md#compose-file-provider-migration).
- A dry run does not regenerate Compose, migrate provider files, prune contexts
  or persist reconciled context metadata.
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
| `--json` | flag | false | Prints validation result, project name, provider type, config path and provider path as JSON. |

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
providers, run `sftpwarden deploy`, `sftpwarden kube apply` or `sftpwarden helm
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
`rsync` or `scp`, `kubectl` and `helm`.

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

Health does not apply runtime changes. It validates config, provider readability, generated
deployment drift, provider schema drift, runtime health when available and
remote availability for remote-only contexts.

If a remote-only context points at a remote root that was manually deleted,
SFTPWarden removes only the stale local registry entry. If the remote server does
not respond, the context is kept and the command reports the SSH connectivity
failure clearly.

## `sftpwarden backup`

Creates a `.tar.gz` backup of operational project state. Deployed installations
require a maintenance window. User files are excluded unless `--include-data`
is supplied; the archive remains sensitive even without them.

```bash
sftpwarden backup --maintenance --output sftpwarden-prod.tar.gz --yes
sftpwarden backup --maintenance --include-data --output full-backup.tar.gz
sftpwarden backup --dry-run --json
sftpwarden backup --maintenance --include-data --output runtime-backup.tar.gz --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--output`, `-o` | `PATH` | timestamped archive name | Archive path to create. |
| `--include-data` | flag | false | Includes SFTP user data under `data/`. Without it, operational state is backed up but user files are excluded. |
| `--maintenance` | flag | false | Authorizes runtime capture and transfer downtime. Requires paused external writers; resumes only a previously running target. |
| `--dry-run` | flag | false | Prints backup entries without writing an archive. |
| `--json` | flag | false | Prints backup result and entries as JSON. |
| `--yes`, `-y` | flag | false | Accepts the `--include-data` confirmation prompt. |

### Effects

Backups may include config, generated deployment files, host keys, runtime state,
raw local provider files and a normalized provider user snapshot. Treat backups
as sensitive.

#### Maintenance capture

For a deployed Compose runtime, pause external
provider/data writers before passing `--maintenance`. This flag explicitly
authorizes downtime: active transfers are terminated, reconciliation is stopped
and a disposable helper reads the stopped runtime's mounts. The helper has no
Docker socket; external database capture uses the runtime network. Capture is bounded. A previously
running container must become healthy again before the archive is published;
a previously stopped container stays stopped. If capture or resumption fails,
the command fails and preserves an existing output file.

Runtime capture stores the applied configuration and generates a Compose file
for recovery into a new project. Pending desired configuration edits and custom
Compose additions are not applied or copied as deployment instructions. The
source project remains available for comparing those settings. The source must
have an applied configuration snapshot and retained runtime identity state.

#### Undeployed projects and other targets

Without `--maintenance`, backup is a project-file/provider snapshot. Nonempty
local `state/` or `host_keys/` directories or nonempty `data/` selected with
`--include-data`, require the maintenance route. The
maintenance route also supports external providers. Remote contexts capture the
remote runtime over SSH and Kubernetes/Helm capture the selected workload's claims
through a temporary helper Pod. Both require maintenance authorization. Legacy
ephemeral cluster identity must be prepared before capture. See the
[recovery runbook](../recovery.md) for prerequisites and recovery scope.

#### Archive validation

New archives carry a SHA-256 member inventory. Missing providers, unsupported
links/special files, unsafe paths and incomplete captures cannot be published as
successful archives. The supported archive subset is regular files and
directories, with at most 100,000 members, 100 GiB expanded and 64 MiB per recovery
metadata document. Checksums detect changes; they do not authenticate the sender
of an archive. Backups contain sensitive recovery material and are created with
private permissions.

## `sftpwarden restore`

Restores a SFTPWarden archive to an inspected destination. A separate empty
`--root` is the simplest recovery path. Restoration does not start the service;
review the result and deploy explicitly.

In-place restore requires read/write access to the selected runtime's protected
host files. On native Linux this normally means running the restore as an
administrator with the intended SFTPWarden home and context. Without that access,
inspection stops before replacement. Recover into a separate empty `--root` to
avoid requiring elevated host filesystem access. See [recovery](../recovery.md).

```bash
sftpwarden restore sftpwarden-prod.tar.gz --maintenance
sftpwarden restore sftpwarden-prod.tar.gz --maintenance --include-data --yes
sftpwarden restore sftpwarden-prod.tar.gz --dry-run --json
sftpwarden restore runtime-backup.tar.gz --root ./recovered --include-data --yes
sftpwarden restore runtime-backup.tar.gz --context prod --maintenance --include-data --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `backup_path` | Yes | `PATH` | Backup archive to restore. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Destination context. |
| `--root` | `PATH` | none | Restores into a verified empty local directory without loading the original installation. Cannot be combined with `--context`. |
| `--provider-context` | `TEXT` | none | With `--root`, selects a separate initialized empty external database. With remote `--context`, selects its separately recovered local project and receipt. |
| `--maintenance` | flag | false | Confirms external writers are paused and authorizes a local Compose runtime restore. Leaves the runtime stopped for explicit deployment. |
| `--include-data` | flag | false | Restores SFTP user data from the archive. |
| `--dry-run` | flag | false | Validates and reports restore entries without overwriting files. |
| `--json` | flag | false | Prints restore result, entries and safety backup path as JSON. |
| `--yes`, `-y` | flag | false | Accepts restore confirmation prompts. |

### Effects

Restoring user data requires explicit confirmation unless `--yes` is used.
Archive validation precedes any safety backup or replacement. The context route
uses a verified safety backup for a readable local destination. Damaged local
targets preserve readable material with an omission inventory, then require
separate recovery. Remote and cluster routes require separate empty destinations.

#### Existing local destinations

For an existing local Compose file provider, restoration inspects destination
paths, rejects links and conflicting layouts and preserves unrelated files.
It publishes each selected file atomically and records completed/pending steps
in the private `.sftpwarden/restore-transaction.json`. If interrupted, keep the
target stopped and recover the journal's safety archive into a separate empty
directory with `--root`. Retain the original target for inspection. A subsequent
in-place attempt is refused while the journal is incomplete.

For a deployed local runtime, pause external provider/data writers and pass
`--maintenance`. The runtime stays stopped throughout safety capture, archive
verification and file replacement. A failure before replacement resumes a
previously running runtime; once replacement starts, it stays stopped even if
the command fails. Incomplete replacement blocks runtime startup. Completed
replacement requires a newly created container through explicit deploy; starting
the old container is refused. The safety archive is retained after success.

This route requires matching provider/config/Compose paths, generated Compose
configuration, the expected project bind mounts and no pending config changes.
The operator needs write access to the target directories. Close external SQLite
connections before replacement. Restoring without user data must preserve the
existing UID/GID mappings. For differing layouts, customized Compose files,
damaged targets or other deployment topologies, use a separately verified
recovery destination; this local maintenance route does not cover them.

#### External databases

Existing external-provider projects require the archive's provider settings and
schema to match the selected context. Restoration connects through the current
context, verifies its safety archive and replaces policy only if the database
still matches that protected snapshot. Changed DSNs, tables, collections or
schemas are rejected before replacement. Deployed external-provider runtimes
require `--maintenance` and the same checked replacement and safety boundary.
MongoDB bulk restoration requires a replica set.

External provider publication is recorded separately from file publication. If
either is interrupted, preserve the database, safety archive and private journal.
Pause external writers, inspect completed/pending steps and extract
`provider/users.json` privately from the verified safety archive. After confirming
the destination, `sftpwarden provider import --input RECOVERY_JSON --replace
--no-refresh --context NAME` can recover its policy. Reconcile the project files
before explicit activation. An empty directory alone does not protect or isolate
an external database. Use `--root --provider-context` to select an initialized,
empty and separately protected database destination.

#### Empty recovery directory

`--root` leaves a damaged original untouched and retains the source archive.
Its parent directory must already exist. The destination must be absent or empty;
unrelated content is rejected. All selected files are staged privately before
publication. No safety backup is needed because the destination has no previous
installation. External providers additionally require `--provider-context`;
without it, the empty filesystem does not authorize database replacement.

#### Activation and verification

Use a format-2 runtime archive and the 1.4.0 runtime for recovery of a deployed
service. The restored state includes a private metadata retry record. At explicit
startup, the Linux runtime verifies file contents and retained user IDs, restores
ownership and modes without following links and only then permits OpenSSH to
start. Private host keys become root-owned `0600`; their directory is `0700`.
Failed metadata application retains its retry record and reports progress in
`state/restore-status.json`.

Restoration reports `files_restored` and `activation_required: true` in JSON.
It does not deploy or claim that service has recovered. Review the recovered
configuration and image, register/select the new context and deploy explicitly.
Then verify health, the previously trusted host-key fingerprint and SFTP
upload/download before removing the original or the source archive. Legacy
format-1 archives remain readable, but do not contain the ownership/checksum
inventory required by this fresh runtime recovery route.

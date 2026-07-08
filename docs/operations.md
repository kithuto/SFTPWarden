# Operations

This guide covers day-to-day commands for local and remote environments.

## Local Runtime

```bash
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
sftpwarden deploy
sftpwarden doctor
```

Preview and apply user changes:

```bash
sftpwarden plan
sftpwarden refresh
```

Check project and runtime health:

```bash
sftpwarden health
sftpwarden health --json
```

`sftpwarden init` sets the created context as active. The recommended workflow is
the Docker-style one: create a project directory, `cd` into it, initialize it, and
run commands without repeating `--context`. Use `sftpwarden context use dev` to
switch later, or pass `--context dev`/`-c dev` for one explicit command.

## Applying Configuration Changes

Changes to `sftpwarden.yaml` are desired-state changes. They can come from
`sftpwarden config PATH VALUE` or from a manual edit, and they are applied by the
next deploy step:

```bash
sftpwarden validate
sftpwarden plan
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

For Kubernetes manifest projects, `sftpwarden kube apply` is the direct apply
command. For Helm projects, use `sftpwarden helm upgrade --install` when you are
not routing through `sftpwarden deploy`.

Deploy regenerates and applies the artifacts controlled by the config: Compose
files, Kubernetes manifests, Helm values, runtime container settings, PVC/probe
settings, provider bootstrap content for Kubernetes YAML/CSV projects, and
forward provider schema migrations requested by `provider.user_schema`.

`sftpwarden refresh` reloads users already visible to the running runtime. It
does not apply config changes. `sftpwarden watch` syncs editable user provider
files for remote `local-sync` contexts and also does not sync `sftpwarden.yaml`.

## Remote Deploy

Remote local-sync:

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical

sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Remote-only:

```bash
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical

sftpwarden refresh --dry-run
```

Remote setup checks verify SSH connectivity and `docker compose version`.
Local deploys also check Docker Compose before applying the generated Compose
file. Source checkouts build `sftpwarden:local`; Python package installations
pull `ghcr.io/kithuto/sftpwarden:<installed-version>`.

Use `sftpwarden context add` when the project already exists on the remote host and
you only need to register it locally:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
sftpwarden context use prod
```

## Context Cleanup

There are two cleanup paths:

- If a project folder is deleted manually, the next context-aware SFTPWarden
  command prunes the stale local registry entry. For remote contexts this is
  local-only cleanup; SFTPWarden does not SSH to the remote host and does not
  remove remote Docker resources or remote files.
- Remote-only contexts have no local project folder. If their remote project
  folder is deleted manually, the next real remote operation such as `deploy`,
  `refresh`, `health`, or `backup` removes only the stale local registry entry.
  If the remote server itself does not respond, SFTPWarden keeps the context and
  reports a controlled connectivity error with an SSH troubleshooting hint.
- If you run `sftpwarden context remove <name>`, SFTPWarden treats that as an
  explicit cleanup request. It removes the local registry entry, deletes the
  project-owned local root when it is not shared with another context, stops the
  local Compose runtime when possible, and updates or uninstalls the watcher when
  needed.

Remote context removal keeps remote files by default in non-interactive mode:

```bash
sftpwarden context remove prod --yes
```

Interactive removal asks whether to delete remote runtime/project data. For CI or
scripts, request that explicitly:

```bash
sftpwarden context remove prod --yes --delete-remote
```

## Kubernetes Deploy

Compose remains the default deployment target. Pick Kubernetes during init when a
project should be managed by `kubectl` or Helm:

```bash
sftpwarden init prod --deploy kube --yes
sftpwarden deploy --dry-run
sftpwarden kube apply
```

Helm mode stores the same target in `sftpwarden.yaml`, but delegates deploys to
the official chart:

```bash
sftpwarden init prod --deploy helm --yes
sftpwarden helm values --write
sftpwarden helm lint
sftpwarden deploy --dry-run
```

When the CLI runs from a source checkout, Helm commands use the local
`charts/sftpwarden` directory so chart changes can be tested before publishing.
When SFTPWarden is installed from the Python package, Helm commands use the
published OCI chart at `oci://ghcr.io/kithuto/charts/sftpwarden` with the same
version as the installed CLI.

You can change the target later:

```bash
sftpwarden config deploy.target kubernetes
sftpwarden config kubernetes.mode helm
sftpwarden config kubernetes.namespace sftpwarden
sftpwarden config kubernetes.kube_context kind-sftpwarden
```

Kubernetes rendering is separate from applying. After a project exists,
`sftpwarden kube render` and `sftpwarden helm values` do not require a live
cluster. `kube apply`, `kube status`, `kube logs`, `kube doctor`, `helm template`,
`helm lint`, `helm upgrade`, and `helm uninstall` require the matching external
tool.

The default Kubernetes namespace is `sftpwarden`. During `init`, SFTPWarden
checks the configured namespace with `kubectl`. If the namespace is missing,
interactive init asks whether to create it; `--yes` accepts namespace creation by
default. Pass `--namespace <name>` when you want a different existing or new
namespace, or use `--no-create-namespace` to abort instead of creating a missing
namespace. Use `sftpwarden config kubernetes.namespace <name>` later when a
cluster policy requires a different namespace.

If you are preparing manifests or values before cluster access is available, run
`init` with `--skip-checks`, then create or select the namespace before the first
deploy.

File-backed providers use a provider PVC. YAML and CSV providers are declarative
for Kubernetes projects: when SFTPWarden writes and applies manifests or Helm
values from a local project, it renders the current local `users.yaml` or
`users.csv` into the deployment and the init container copies that content into
the provider PVC during the runtime rollout. That means the users in the cluster
match the provider file that was deployed. Treat the rendered manifest or Helm
values as operational deployment material because they include the YAML/CSV user
entries that will be copied to the provider PVC.

`sftpwarden refresh` reloads users that are already visible inside the running
runtime. It does not copy local YAML/CSV files into Kubernetes by itself, and the
watcher is only for remote `local-sync` contexts. For YAML/CSV on Kubernetes,
use `sftpwarden deploy`, `sftpwarden kube apply`, or
`sftpwarden helm upgrade --install` after changing the local provider file.
SQLite provider PVCs are initialized as a database file for single-pod lab use,
but local SQLite files are not declaratively copied into Kubernetes.

The SFTP user data PVC defaults to `10Gi`. Increase it through project config
and deploy the generated manifests or values:

```bash
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden plan
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

`sftpwarden plan` reports `kubernetes.yml` or `values.yaml` drift after the
change. `sftpwarden deploy` updates the PVC request and restarts the runtime
StatefulSet so the mounted volume is remounted. Your StorageClass must allow
volume expansion; Kubernetes does not shrink existing PVCs.

Runtime healthcheck timing is also configurable. Compose projects use
`healthcheck.*`; Kubernetes manifest and generated Helm projects use the
`kubernetes.*_probe.*` settings:

```bash
sftpwarden config healthcheck.interval_seconds 45
sftpwarden config healthcheck.timeout_seconds 15
sftpwarden config kubernetes.startup_probe.failure_threshold 60
sftpwarden config kubernetes.liveness_probe.period_seconds 45
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

`sftpwarden plan` reports Compose, manifest, or values drift after these changes.
`sftpwarden deploy` regenerates the deployment files and restarts or recreates
the runtime as needed for the active deployment target.

Database providers should receive DSNs through a Kubernetes Secret. In Helm,
set `provider.dsnSecretName` and reference the same environment variable from
`sftpwardenConfig`; prefer creating the Secret outside the values file for
production deployments.

SFTPWarden v1.3 supports one runtime pod per context. `kubernetes.replicas` and
Helm `runtime.replicas` are reserved for future multi-node support and currently
accept only `1`.

The runtime entrypoint clamps the inherited open-file limit to `65536` by
default before starting OpenSSH. This avoids container platforms that set an
extremely high `nofile` limit causing `internal-sftp` chroot sessions to stall
while closing file descriptors. Override `SFTPWARDEN_NOFILE_LIMIT` only when your
platform has a specific reason to use a different value.

For serious Kubernetes environments, use PostgreSQL, MariaDB/MySQL, or MongoDB.
The runtime reads those providers directly, so changes written to the database
are available to the runtime sync loop and can also be forced with
`sftpwarden refresh`. YAML/CSV fit GitOps-style deployments where deploy is the
sync point, especially key-only or small reviewed environments. SQLite is
acceptable only for single-pod lab deployments and should not be used for
multi-writer production workloads.

## Watcher

`sftpwarden watch` is only for remote `local-sync` contexts. It syncs
YAML/CSV/SQLite user provider files to remote hosts. It does not sync
`sftpwarden.yaml`.

```bash
sftpwarden watcher status
sftpwarden watcher install --dry-run
sftpwarden watcher install --watcher systemd --dry-run
sftpwarden watcher install --watcher docker --yes
sftpwarden watcher uninstall --yes
```

Watched files are derived from the context registry and provider configuration.
Configuration, Compose, Kubernetes, and Helm changes require an explicit deploy.

Watcher installation defaults to `auto`. SFTPWarden detects the local host and
chooses the first supported native scheduler:

- Windows: Task Scheduler.
- macOS: launchd.
- Linux: systemd, OpenRC, runit, then supervisord.

Use a concrete backend when operations policy requires one:

```bash
sftpwarden watcher install --watcher systemd
sftpwarden watcher install --watcher openrc
sftpwarden watcher install --watcher runit
sftpwarden watcher install --watcher supervisord
sftpwarden watcher install --watcher launchd
sftpwarden watcher install --watcher windows-task
```

Watcher install writes the generated backend file and activates it by default.
Use `--dry-run` to review the scheduler commands first, or `--no-activate` when
you only want SFTPWarden to render the file. Linux native scheduler backends
install service files under system locations, so their activation and uninstall
commands use `sudo` and may ask for the host user's sudo password.

Native watcher modes run `sftpwarden watch` on the host and use the host's default
SSH identity, agent, SSH config, known hosts, bastions, and `ProxyJump`. This is
the recommended production shape when those SSH features matter. Windows native
watcher sync uses OpenSSH `scp` for the single provider file; Linux and macOS use
`rsync`.

If no native scheduler is detected, interactive installs ask whether to use the
Docker watcher. With `--yes`, Docker fallback is accepted automatically. In
non-interactive use, pass `--watcher docker` explicitly when that is the intended
mode.

Docker watcher mode writes a Docker-specific context registry with Linux
container paths, mounts local project folders read-only, and mounts only explicit
dedicated SSH keys. The entrypoint copies those keys inside the container with
private permissions before syncing. It does not mount `~/.ssh` and does not
require Docker socket access. Source checkouts build `sftpwarden-watcher:local`;
Python package installations use
`ghcr.io/kithuto/sftpwarden-watcher:<installed-version>` unless `--image` is set.

`sftpwarden watcher uninstall` deactivates the scheduler backend, removes the
generated watcher file, and clears SFTPWarden's watcher metadata. Installing a
different watcher backend first deactivates the old backend, then writes and
activates the new one. Removing or pruning the last remote `local-sync` context
removes the watcher automatically; if a Docker watcher remains installed because
other local-sync contexts still exist, SFTPWarden refreshes its generated context
metadata.

## Provider Transfer

Use provider transfer commands when you are moving users between storage backends,
creating a portable snapshot, or copying users from one context to another.

Export users:

```bash
sftpwarden provider export --format json > users.json
sftpwarden provider export --output users.yaml
```

Import users into the active context:

```bash
sftpwarden provider import --input users.json --merge
sftpwarden provider import --input users.yaml --replace --dry-run
```

Copy users between contexts:

```bash
sftpwarden provider copy \
  --from-context dev \
  --to-context prod \
  --merge \
  --dry-run
```

`--merge` upserts source users and keeps destination-only users. `--replace`
makes the destination exactly match the source. Provider transfer refreshes only
when runtime-relevant user fields change; comment-only changes do not trigger a
refresh. Kubernetes YAML/CSV destinations report that a deploy is required,
because the provider PVC is synchronized during deploy rather than by refresh.

## Backup and Restore

Create a project backup:

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
```

Restore a backup:

```bash
sftpwarden restore sftpwarden-prod.tar.gz --yes
```

Backups include project config, Compose file, `provider/users.json` with the
current users read from the provider, raw local provider files when available,
host keys, and runtime state. SQL and MongoDB providers are captured through that
JSON user snapshot when the CLI can reach the configured database. SFTP user data
under `data/` is excluded unless you pass `--include-data`.

Backups may contain secrets if DSNs or environment references are stored in
`sftpwarden.yaml`. Store backup archives with the same care as infrastructure
secrets.

## User Schema Migration

Schema v1 keeps simple `public_keys` on each user. Schema v2 adds named keys and
per-key lifecycle metadata. Inspect and migrate explicitly:

```bash
sftpwarden provider schema show
sftpwarden provider keys migrate --dry-run
sftpwarden provider schema migrate --to 2 --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

Advanced key commands such as `disable`, `rename`, `rotate`, `expire`, and
`import` prompt before migrating a v1 provider to v2. Ordinary reads never
rewrite provider data. Mutable migrations create a logical YAML backup by
default unless `--no-backup` is used.

Changing `provider.user_schema` in `sftpwarden.yaml` does not migrate provider
data immediately. The config command warns and asks before accepting a change
that requires migration; manual edits are detected later. The next
`sftpwarden deploy`, `sftpwarden kube apply`, or `sftpwarden helm upgrade`
performs the forward migration before applying deployment changes, asks for
confirmation unless `--yes` is used, and reports the backup path. If the config
asks for an older schema than the provider data already uses, the command fails
instead of downgrading.

## Runtime State

Runtime state lives at `/var/lib/sftpwarden/state.json` inside the container and
should be backed by the `state/` volume.

Host keys live in `/etc/sftpwarden/host_keys` and should be backed by `host_keys/`
so server fingerprints do not change on restart.

## Deleting User Data

By default, removing a user removes the provider entry and disables access after
refresh. User files remain on disk:

```bash
sftpwarden user remove alice --yes
```

Use the explicit delete flag only when the data should be destroyed:

```bash
sftpwarden user remove alice --delete-files --yes
```

## Troubleshooting

Runtime is not running:

```bash
docker compose ps
sftpwarden deploy
sftpwarden refresh
```

Remote checks fail:

```bash
ssh deploy@example.com true
ssh deploy@example.com 'docker compose version'
```

If `docker compose version` fails locally or remotely, install Docker Compose v2
before running `sftpwarden deploy` again. For remote-only contexts, also verify
that the registered remote root still exists. If it was removed intentionally,
recreate the remote project or register a new context.

Provider data changed but users did not update:

```bash
sftpwarden plan
sftpwarden refresh --dry-run
sftpwarden refresh
```

For Kubernetes YAML/CSV providers, use deploy/apply/upgrade instead because the
local provider file must be copied into the provider PVC:

```bash
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Healthcheck fails in Docker Compose:

```bash
sftpwarden health --json
docker compose exec sftpwarden sftpwarden runtime health --json
```

The generated Docker Compose file uses `sftpwarden runtime health` as its
container healthcheck. Tune its timing with `healthcheck.interval_seconds`,
`healthcheck.timeout_seconds`, `healthcheck.retries`, and
`healthcheck.start_period_seconds`, then run `sftpwarden deploy`.

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

Kubernetes rendering is separate from applying. `sftpwarden kube render` and
`sftpwarden helm values` do not require a live cluster. `kube apply`, `kube
status`, `kube logs`, `kube doctor`, `helm template`, `helm lint`, `helm upgrade`,
and `helm uninstall` require the matching external tool.

The default Kubernetes namespace is `sftpwarden`. Use
`sftpwarden config kubernetes.namespace <name>` when a cluster policy requires a
different namespace.

File-backed providers use a provider PVC. The Kubernetes init container creates
an empty YAML/CSV provider file when the PVC is new, and never overwrites an
existing provider file.

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

SFTPWarden v1.2 supports one runtime pod per context. `kubernetes.replicas` and
Helm `runtime.replicas` are reserved for future multi-node support and currently
accept only `1`.

For serious Kubernetes environments, use PostgreSQL, MariaDB/MySQL, or MongoDB.
YAML/CSV fit GitOps-style deployments. SQLite is acceptable only for single-pod
lab deployments and should not be used for multi-writer production workloads.

## Watcher

`sftpwarden watch` is only for remote `local-sync` contexts. It syncs
YAML/CSV/SQLite user provider files to remote hosts. It does not sync
`sftpwarden.yaml`.

```bash
sftpwarden watcher status
sftpwarden watcher install --watcher systemd --dry-run
sftpwarden watcher install --watcher docker --yes
sftpwarden watcher uninstall --yes
```

Watched files are derived from the context registry and provider configuration.
Configuration, Compose, Kubernetes, and Helm changes require an explicit deploy.

Systemd watcher installation uses `sudo` for service setup and enables the service
with `systemctl enable --now`. Use this mode for production when SSH should use
the host's default identity, agent, SSH config, bastions, or `ProxyJump`.

Docker watcher mode mounts the context registry, local project folders, and only
explicit dedicated SSH keys read-only. It does not mount `~/.ssh` and does not
require Docker socket access. Source checkouts build `sftpwarden-watcher:local`;
Python package installations use
`ghcr.io/kithuto/sftpwarden-watcher:<installed-version>` unless `--image` is set.

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
refresh.

## Backup and Restore

Create a project backup:

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
```

Restore a backup:

```bash
sftpwarden restore sftpwarden-prod.tar.gz --yes
```

Backups include project config, Compose file, provider snapshot, raw local
provider files when available, host keys, and runtime state. SFTP user data under
`data/` is excluded unless you pass `--include-data`.

Backups may contain secrets if DSNs or environment references are stored in
`sftpwarden.yaml`. Store backup archives with the same care as infrastructure
secrets.

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
before running `sftpwarden deploy` again.

Provider data changed but users did not update:

```bash
sftpwarden plan
sftpwarden refresh --dry-run
sftpwarden refresh
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

# Operations

Use this guide after [Getting Started](getting-started.md). Commands run on your
administrator computer unless a section says otherwise.

Before changing an installation, confirm the active context with
`sftpwarden context show`. Changing your shell directory does not switch the
active context. Use `sftpwarden context use NAME` or pass `--context NAME`
where supported.

| You changed… | Next action |
| --- | --- |
| A user through the CLI | The command normally refreshes automatically. |
| Users in a provider outside the CLI | Automatic synchronization applies them; refresh if you want to request it now. |
| A local file for a remote local-sync project | Let the watcher deliver it and refresh. |
| A local YAML/CSV file for Kubernetes | Deploy to deliver the file to the Pod. |
| Project or deployment configuration | Preview and deploy explicitly. |
| Only a user's comment | No runtime refresh is needed. |

See [Configuration Effects](configuration-effects.md) for exceptions and checks.

## Automatic User Synchronization

SFTPWarden keeps SFTP accounts aligned with your configured provider. Add a user
when someone needs access, update their keys or disable their account when that
access is no longer needed. You do not need to rebuild the runtime image or
restart the server to apply users already available through its provider.

There are two ways to manage the same account source:

- **Use the CLI:** `sftpwarden user create bob` prompts for a password, saves the
  account and normally requests an immediate refresh.
- **Edit the provider directly:** add or update a record in the active YAML/CSV
  file or database. This also works for changes made by another application.
  Follow the provider schema and use password hashes or valid public keys.

Automatic synchronization is enabled by default through `sync.enabled`. Its
periodic interval, `sync.interval_seconds`, defaults to 60 seconds. Changes are
not guaranteed to be instantaneous; use `sftpwarden refresh` to request an update
without waiting. Changing these settings requires deploy. If you disable
automatic synchronization, external edits require an explicit refresh.

### How changes reach the server

| Deployment | How provider changes become available |
| --- | --- |
| Local Compose with YAML/CSV/SQLite | Edit the active file in `provider/`; the runtime sees it through its mounted directory. |
| External database | The runtime reads the configured database directly. |
| Remote local-sync with a file provider | The installed watcher delivers local provider changes and requests refresh when access changes. |
| Remote-only with a file provider | Edit the active provider on the remote host; its runtime handles synchronization. |
| Kubernetes/Helm with local YAML/CSV | Deploy to deliver the changed file to the Pod. The runtime cannot read an undeployed local edit. |

Check `sftpwarden health` and test a new SFTP connection after an access change.
Malformed or unreadable provider data reports a failure and preserves the last
applied policy. Disabling an account affects new logins; it does not terminate
existing sessions. Removing an account does not delete its uploaded files by
default.

## Updating SFTPWarden

Use a staged update when the replacement watcher is compatible with your servers:

```bash
sftpwarden upgrade --check
sftpwarden upgrade --dry-run
sftpwarden upgrade
sftpwarden deploy --context prod --dry-run
sftpwarden deploy --context prod
```

Upgrade updates management tools and reports which servers still need deployment.
Deploy inspects the running version, prepares required migrations and verifies the
activated runtime. Official images advance; custom images remain under their
maintainer's control. Review the backup recommendation before confirming.

An incompatible watcher requires a coordinated update: the command lists the
incompatibility and asks to deploy **every registered context** before starting
the replacement watcher. This can interrupt transfers. In automation, only
`--yes --deploy-all` authorizes that expanded scope; `--yes` alone refuses it.
The 1.3.0 file-delivery protocol and legacy single-file mounts require this path.

On Windows, the initial response can report a background update in progress.
Use `sftpwarden upgrade --status` to check its completion. See the
[command reference](cli/upgrade.md) for supported installers, custom images and
failure recovery, and [Recovery](recovery.md#upgrade-from-130) for legacy backups.

### Release notices

Successful interactive deploy, Kubernetes apply, Helm upgrade and watcher install
commands can announce a newer completed release. Notices are absent from JSON,
noninteractive output and runtime/watch loops. A failed check does not fail the
original command. Network checking is bounded to approximately two seconds;
successful results are cached for 24 hours and failed attempts for one hour.

Disable advisory checks with `SFTPWARDEN_NO_UPDATE_CHECK=1` or in the global config:

```toml
[updates]
check_enabled = false
```

Explicit `upgrade` checks still request fresh release information.

## Local Runtime

For an existing tutorial installation:

```bash
sftpwarden context use demo
sftpwarden health
sftpwarden users
```

Health should report a running, healthy runtime. The user list reads the provider;
test an SFTP connection when you need to prove actual access.

### Add or disable an account

Create an account with an interactively entered password:

```bash
sftpwarden user create bob
```

The command writes the provider and refreshes access. Verify a new connection as
Bob. For SSH keys, follow [Named Keys](named-keys.md).

To revoke a user's new connections while keeping their data:

```bash
sftpwarden user disable bob
```

A new login should fail. Existing sessions continue; see
[revocation and expiry](named-keys.md#revocation-and-expiry) if they must end now.
Use `sftpwarden user enable bob` to restore the account's configured access.

### Apply an external provider edit

Save the change in the active provider and let automatic synchronization apply
it. To inspect the change and apply it immediately instead:

```bash
sftpwarden plan
sftpwarden refresh --dry-run
sftpwarden refresh
sftpwarden health
```

If refresh reports invalid provider data, correct that data first. The runtime
keeps the last successfully applied policy instead of interpreting an unreadable
provider as an empty list of accounts.

## Applying Configuration Changes

Run `validate` from the project directory or provide its `--config` path.
It checks configuration and provider location, not a live database connection.
Deployment may interrupt transfers, so schedule changes to a shared server.

Changes to `sftpwarden.yaml` are desired-state changes. They can come from
`sftpwarden config PATH VALUE` or from a manual edit and they are applied by the
next deploy step:

```bash
sftpwarden validate
sftpwarden plan
sftpwarden deploy --dry-run
sftpwarden deploy
```

For Kubernetes manifest projects, `sftpwarden kube apply` is the direct apply
command. For Helm projects, use `sftpwarden helm upgrade --install` when you are
not routing through `sftpwarden deploy`.

Deploy regenerates and applies the artifacts controlled by the config: Compose
files, Kubernetes manifests, Helm values, runtime container settings, PVC/probe
settings, provider bootstrap content for Kubernetes YAML/CSV projects and
forward provider schema migrations requested by `provider.user_schema`.

`sftpwarden refresh` reloads users already visible to the running runtime. It
does not apply config changes. `sftpwarden watch` syncs editable user provider
files for remote `local-sync` contexts and also does not sync `sftpwarden.yaml`.

## Remote Deploy

Choose where the editable project should live:

| Mode | Source of project files | Best fit |
| --- | --- | --- |
| Local-sync | Administrator computer | Manage files locally and deliver them over SSH. |
| Remote-only | Remote server | Operate a project maintained on that server. |

You need SSH access to a Docker host with Compose v2, permission to manage its
containers and a writable project destination. Replace `deploy@example.com`
with your deployment account. This is separate from an SFTP user such as Alice.

### Create a local-sync project

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

After deployment, run `sftpwarden health` and connect to the remote server's
published SFTP port. Check firewall rules and the configured bind address.

### Register a remote-only project

Use this alternative when the project already exists on the remote server.
Remote-only init registers its location; it does not create remote project files:

```bash
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden-archive \
  --remote-only \
  --critical

sftpwarden deploy --context archive --dry-run
sftpwarden deploy --context archive
sftpwarden health --context archive
```

Remote-only contexts have no local provider to edit. Manage accounts from the
project on the server or through its external database. The watcher is not used.

Remote setup checks verify SSH connectivity and `docker compose version`.
Local deploys also check Docker Compose before applying the generated Compose
file. Source checkouts build `sftpwarden:local`; Python package installations
pull `ghcr.io/kithuto/sftpwarden:<installed-version>`.

Use `sftpwarden context add` when the project already exists on the remote host and
you only need to register it locally:

```bash
sftpwarden context add existing deploy@example.com:/opt/existing-sftp --remote-only --critical
sftpwarden context use existing
```

## Context Cleanup

Removing a context can delete its project directory and stop its runtime.
Use `context clear` if you only want to clear the active selection.

There are three cleanup cases:

- If a project folder is deleted manually, the next context-aware SFTPWarden
  command prunes the stale local registry entry. For remote contexts this is
  local-only cleanup; SFTPWarden does not SSH to the remote host and does not
  remove remote Docker resources or remote files.
- Remote-only contexts have no local project folder. If their remote project
  folder is deleted manually, the next real remote operation such as `deploy`,
  `refresh`, `health` or `backup` removes only the stale local registry entry.
  If the remote server itself does not respond, SFTPWarden keeps the context and
  reports a controlled connectivity error with an SSH troubleshooting hint.
- If you run `sftpwarden context remove <name>`, SFTPWarden treats that as an
  explicit cleanup request. It removes the local registry entry, deletes the
  project-owned local root when it is not shared with another context, stops the
  local Compose runtime when possible and updates or uninstalls the watcher when
  needed.

Remote context removal keeps remote files by default in non-interactive mode:

```bash
sftpwarden context remove prod --yes
```

Interactive removal asks whether to delete remote runtime/project data. For CI or
scripts, the following alternative also destroys the remote project and runtime
data. Back up anything you need before using it:

```bash
sftpwarden context remove prod --yes --delete-remote
```

## Kubernetes Deploy

Use Kubernetes when you already operate a cluster with persistent storage. You
need `kubectl` access to the intended cluster, a suitable StorageClass and a
runtime image the nodes can pull. Helm mode also needs Helm.

Choose **one** of the following alternatives in a new project directory.

### Kubernetes manifests

```bash
mkdir -p ~/sftpwarden-cluster
cd ~/sftpwarden-cluster
sftpwarden init cluster --deploy kube --yes
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden kube apply
```

### Helm

Helm mode stores the same target in `sftpwarden.yaml`, but delegates deploys to
the official chart:

```bash
mkdir -p ~/sftpwarden-helm
cd ~/sftpwarden-helm
sftpwarden init helm-demo --deploy helm --yes
sftpwarden user create alice --no-refresh
sftpwarden helm values --write
sftpwarden helm lint
sftpwarden deploy --dry-run
sftpwarden helm upgrade --install
```

After either workflow, run `sftpwarden kube status` and `sftpwarden health`.
Find the Service address with `kubectl get service -n sftpwarden`. With the
default ClusterIP Service, use a separate terminal for a local test:

```bash
kubectl port-forward --namespace sftpwarden service/cluster 2222:2222
```

This uses the manifest example's `cluster` release. For the Helm example, use
`service/helm-demo`. Leave that process running while connecting with
`sftp -P 2222 alice@127.0.0.1`. Adjust the Service name or port if you changed
them. This forwarding is for a local test; permanent exposure depends on your
cluster networking.

### Deployment details

When the CLI runs from a source checkout, Helm commands use the local
`charts/sftpwarden` directory so chart changes can be tested before publishing.
When SFTPWarden is installed from the Python package, Helm commands use the
published OCI chart at `oci://ghcr.io/kithuto/charts/sftpwarden` with the same
version as the installed CLI.

Changing deployment targets does not migrate existing files, identity or state.
Plan a recovery/migration procedure before moving an existing server. To configure
a project for a particular target, set its fields before deployment:

```bash
sftpwarden config deploy.target kubernetes
sftpwarden config kubernetes.mode helm
sftpwarden config kubernetes.namespace sftpwarden
sftpwarden config kubernetes.kube_context kind-sftpwarden
```

Kubernetes rendering is separate from applying. After a project exists,
`sftpwarden kube render` and `sftpwarden helm values` do not require a live
cluster. `kube apply`, `kube status`, `kube logs`, `kube doctor`, `helm template`,
`helm lint`, `helm upgrade` and `helm uninstall` require the matching external
tool.

The default Kubernetes namespace is `sftpwarden`. During `init`, SFTPWarden
checks the configured namespace with `kubectl`. If the namespace is missing,
interactive init asks whether to create it; `--yes` accepts namespace creation by
default. Pass `--namespace <name>` when you want a different existing or new
namespace or use `--no-create-namespace` to abort instead of creating a missing
namespace. Use `sftpwarden config kubernetes.namespace <name>` later when a
cluster policy requires a different namespace.

If you are preparing manifests or values before cluster access is available, run
`init` with `--skip-checks`, then create or select the namespace before the first
deploy.

### Provider delivery and storage

File-backed providers use a provider PVC. YAML and CSV providers are declarative
for Kubernetes projects: when SFTPWarden writes and applies manifests or Helm
values from a local project, it renders the current local `users.yaml` or
`users.csv` into the deployment and the init container copies that content into
the provider PVC during the runtime rollout. That means the users in the cluster
match the provider file that was deployed. Treat the rendered manifest or Helm
values as operational deployment material because they include the YAML/CSV user
entries that will be copied to the provider PVC.

`sftpwarden refresh` reloads users that are already visible inside the running
runtime. It does not copy local YAML/CSV files into Kubernetes by itself and the
watcher is only for remote `local-sync` contexts. For YAML/CSV on Kubernetes,
use `sftpwarden deploy`, `sftpwarden kube apply` or
`sftpwarden helm upgrade --install` after changing the local provider file.
SQLite requires an explicitly initialized provider PVC with the configured schema.
An empty PVC fails startup; local SQLite files are not declaratively copied into
Kubernetes. Use YAML/CSV or an external database for ordinary cluster bootstrap.

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

### Health probes

Runtime healthcheck timing is also configurable. Compose projects use
`healthcheck.*`; Kubernetes manifest and generated Helm projects use the
`kubernetes.*_probe.*` settings:

```bash
sftpwarden config kubernetes.startup_probe.failure_threshold 60
sftpwarden config kubernetes.liveness_probe.period_seconds 45
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

`sftpwarden plan` reports Compose, manifest or values drift after these changes.
`sftpwarden deploy` regenerates the deployment files and restarts or recreates
the runtime as needed for the active deployment target.

Database providers should receive DSNs through a Kubernetes Secret. In Helm,
set `provider.dsnSecretName` and reference the same environment variable from
`sftpwardenConfig`; prefer creating the Secret outside the values file for
production deployments.

SFTPWarden v1.4.0 supports one runtime pod per context. `kubernetes.replicas` and
Helm `runtime.replicas` are reserved for future multi-node support and currently
accept only `1`.

The runtime entrypoint clamps the inherited open-file limit to `65536` by
default before starting OpenSSH. This avoids container platforms that set an
extremely high `nofile` limit causing `internal-sftp` chroot sessions to stall
while closing file descriptors. Override `SFTPWARDEN_NOFILE_LIMIT` only when your
platform has a specific reason to use a different value.

For Kubernetes environments where user changes must be independent of deployments, use PostgreSQL, MariaDB/MySQL or MongoDB.
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

First check whether initialization already installed the watcher:

```bash
sftpwarden watcher status
```

If installation is needed, preview and install the automatically selected backend:

```bash
sftpwarden watcher install --dry-run
sftpwarden watcher install
sftpwarden watcher status
```

Edit an account through the CLI and verify both delivery and a fresh SFTP login
on the remote server. A running scheduler alone does not prove that access changed.

Watched files are derived from the context registry and provider configuration.
Configuration, Compose, Kubernetes and Helm changes require an explicit deploy.

Watcher installation defaults to `auto`. SFTPWarden detects the local host and
chooses the first supported native scheduler:

- Windows: Task Scheduler.
- macOS: launchd.
- Linux: systemd, OpenRC, runit, then supervisord.

Use `--watcher BACKEND` when policy requires a specific scheduler, for example
`sftpwarden watcher install --watcher systemd` on a systemd host. These backends
are alternatives; do not install each one.

Watcher install writes the generated backend file and activates it by default.
Use `--dry-run` to review the scheduler commands first or `--no-activate` when
you only want SFTPWarden to render the file. Linux native scheduler backends
install service files under system locations, so their activation and uninstall
commands use `sudo` and may ask for the host user's sudo password.

### Delivery and backend details

Native watcher modes run `sftpwarden watch` on the host and use the host's default
SSH identity, agent, SSH config, known hosts, bastions and `ProxyJump`. This is
the recommended production shape when those SSH features matter. Provider delivery
streams a private snapshot over SSH, verifies its digest and publishes it atomically
on the remote Linux host. This path is shared by Windows, macOS and Linux clients.
Delivery and application have separate persistent checkpoints. Failed targets retry
without another edit and do not prevent other targets from progressing. Metadata-only
edits are delivered without a runtime refresh.

If no native scheduler is detected, interactive installs ask whether to use the
Docker watcher. With `--yes`, Docker fallback is accepted automatically. In
non-interactive use, pass `--watcher docker` explicitly when that is the intended
mode.

Docker watcher mode writes a Docker-specific context registry with Linux
container paths, mounts local project folders read-only and mounts only explicit
dedicated SSH keys. The entrypoint copies those keys inside the container with
private permissions before syncing. It does not mount `~/.ssh` and does not
require Docker socket access. Checkpoints and locks retain the host operator's
ownership and private permissions, so CLI inspection and a later switch to a
native watcher can reuse them. Source checkouts build `sftpwarden-watcher:local`;
Python package installations use
`ghcr.io/kithuto/sftpwarden-watcher:<installed-version>` unless `--image` is set.

`sftpwarden watcher uninstall` deactivates the scheduler backend, removes the
generated watcher file and clears SFTPWarden's watcher metadata. Installing a
different watcher backend first deactivates the old backend, then writes and
activates the new one. Removing or pruning the last remote `local-sync` context
removes the watcher automatically; if a Docker watcher remains installed because
other local-sync contexts still exist, SFTPWarden refreshes its generated context
metadata.

## Provider Transfer

Use provider transfer commands when you are moving users between storage backends,
creating a portable snapshot or copying users from one context to another.

Export users:

```bash
sftpwarden provider export --format json --output users.json
```

The snapshot contains credential material. Protect it like a backup.
Select the destination context, then preview and import:

```bash
sftpwarden provider import --input users.json --merge --dry-run
sftpwarden provider import --input users.json --merge
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

For a deployed service, pause the watcher and external provider/data writers, then
authorize a bounded maintenance window:

```bash
sftpwarden backup --maintenance --include-data --output sftpwarden-prod.tar.gz --yes
```

Recover into a separate empty local directory:

```bash
sftpwarden restore sftpwarden-prod.tar.gz --root ./recovered --include-data --yes
```

Capture selects actual runtime mounts and applied configuration for local Compose,
remote local-sync, remote-only and Kubernetes/Helm contexts. User files are included
only with `--include-data`. A required snapshot or resumption failure prevents
publication. A file restore requires a separate deploy and SFTP verification before
it counts as recovered service. Treat every archive as secret material.

See [Recovery and upgrades](recovery.md) for destination classification, external
database isolation, cluster identity preparation and interruption handling.

## User Schema Migration

Schema v1 keeps simple `public_keys` on each user. Schema v2 adds named keys and
per-key lifecycle metadata. Inspect and migrate explicitly:

```bash
sftpwarden provider schema show
sftpwarden provider keys migrate --dry-run
sftpwarden provider schema migrate --to 2 --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

Advanced key commands such as `disable`, `rename`, `rotate`, `expire` and
`import` prompt before migrating a v1 provider to v2. Ordinary reads never
rewrite provider data. Mutable migrations create a logical YAML backup by
default unless `--no-backup` is used.

Changing `provider.user_schema` in `sftpwarden.yaml` does not migrate provider
data immediately. The config command warns and asks before accepting a change
that requires migration; manual edits are detected later. The next
`sftpwarden deploy`, `sftpwarden kube apply` or `sftpwarden helm upgrade`
performs the forward migration before applying deployment changes, asks for
confirmation unless `--yes` is used and reports the backup path. If the config
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

As an alternative to the command above, use the explicit delete flag only when
the data should be destroyed:

```bash
sftpwarden user remove alice --delete-files --yes
```

## Troubleshooting

Start with the symptom. Keep error output private if it includes provider or
deployment details.

| Symptom | Check | Next step |
| --- | --- | --- |
| CLI operates on the wrong installation | `sftpwarden context show` | Select the intended context explicitly. |
| Cannot connect to local SFTP | `sftpwarden health` and `docker compose ps` | Check runtime startup, published port and firewall. |
| Remote operation cannot connect | SSH access and `docker compose version` on that host | Fix deployment-account access or install Compose v2. |
| User edit has no effect | `sftpwarden plan` and `sftpwarden refresh --dry-run` | Check provider validity and whether delivery or deploy is required. |
| Kubernetes YAML/CSV edit is missing | Current deployed provider file | Deploy the updated local file; refresh cannot upload it. |
| Health reports configuration drift | `sftpwarden deploy --dry-run` | Review and apply the pending configuration. |
| Server fingerprint changed | Fingerprints from the trusted runtime | Investigate identity or endpoint changes before accepting a new key. |

For more runtime detail in a local Compose project:

```bash
sftpwarden health --json
docker compose exec sftpwarden sftpwarden runtime health --json
```

Do not increase health timeouts to hide a provider or synchronization failure.
Timing controls are documented in [Configuration](configuration.md); process and
synchronization checks are described in [Runtime Commands](cli/runtime.md).

If a remote-only root was intentionally deleted, register or create the replacement
project. An unreachable host is not evidence that the old project is gone.

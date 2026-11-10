# Configuration

Configuration controls how your SFTP server runs: which port clients connect to,
which authentication methods it accepts and where it reads user accounts.

Start with the project created in [Getting Started](getting-started.md).
The walkthrough below changes its local SFTP port from **2222** to **2200**,
checks a real connection and shows how to change it back.

If you are looking up a particular field, go directly to
[Project Variables](#project-variables), [Global Variables](#global-variables)
or [Context Registry Variables](#context-registry-variables).

## Where Settings Live

| Layer | Location | What it controls |
| --- | --- | --- |
| Project | `sftpwarden.yaml` inside the project directory | Settings for one installation, such as its port, authentication and provider |
| Context registry | `~/.sftpwarden/contexts.toml` | The registered environments and which one commands select |
| Global defaults | `~/.sftpwarden/config.toml` | Defaults used by CLI workflows, including provider selection for new projects |

For a first installation, work with the **project settings**. Use the CLI to
inspect and update them; you do not need to edit the global files.

A setting such as `server.port` means the `port` value inside the `server`
section of the YAML file. Passing a value saves it; omitting the value reads it.
Editing YAML and using the CLI change the same project configuration.

**Saving a setting does not apply it to a running server.** Use `deploy` to
activate configuration changes. `refresh` applies user information already
visible to the runtime; it does not apply a new port or SSH server setting.

## Change the Local SFTP Port

This exercise uses the local Compose `demo` project and Alice's password from
[Getting Started](getting-started.md). Have the demo running, leave port 2200
available and close active SFTP sessions: deployment recreates the container
and can interrupt connections.

### 1. Select and inspect the project

Run these commands in your normal terminal:

```bash
cd ~/sftpwarden-demo
sftpwarden context use demo
sftpwarden config server.host
sftpwarden config server.port
docker compose port sftpwarden 22
```

For the tutorial project, the saved host and port should be `127.0.0.1` and
`2222`; Docker should report `127.0.0.1:2222`.
The CLI shows saved settings, while Docker shows the binding actually in use.
If these are not the expected values, check the selected project before changing it.

The container listens on port **22**. `server.port` selects the port published
on your computer, which is the one your SFTP client uses.

### 2. Save and preview the change

```bash
sftpwarden config server.port 2200
sftpwarden validate
sftpwarden deploy --dry-run
```

The setting is now saved in `sftpwarden.yaml`. Validation checks the desired
configuration and the dry run previews deployment without applying it.
The running server still uses port 2222 until you deploy.

### 3. Apply and verify

```bash
sftpwarden deploy
sftpwarden health
docker compose port sftpwarden 22
```

Health should succeed and Docker should now report `127.0.0.1:2200`.
Check that Alice can still connect:

```bash
docker compose exec sftpwarden sh -c 'for key in /etc/sftpwarden/host_keys/*.pub; do ssh-keygen -lf "$key"; done'
sftp -P 2200 alice@127.0.0.1
```

SSH may ask you to trust the address with its new port. Compare the fingerprint
with the container output for the same key type as in the tutorial. The server
key should be unchanged.
Enter Alice's password when prompted. If you completed the optional key setup,
you can add `-i ./alice-key` to connect with that key instead.
At the `sftp>` prompt, retrieve the file you uploaded earlier:

```text
get upload/hello.txt received-after-port-change.txt
bye
```

Back in your terminal:

```bash
cmp hello.txt received-after-port-change.txt
```

No output and a successful exit confirm that the file still matches.
Changing the published port does not move or delete the project's stored data.

### 4. Return to the tutorial port

If you want to keep using the earlier examples unchanged:

```bash
sftpwarden config server.port 2222
sftpwarden deploy
sftpwarden health
docker compose port sftpwarden 22
```

Docker should report `127.0.0.1:2222` again. Reconnect with the original tutorial
command. If you keep port 2200 instead, use `-P 2200` in future client commands.

If deployment fails because a port is occupied, choose an available one or
restore the previous saved port and deploy again. A saved value alone does not
prove that deployment succeeded; inspect Docker's binding and test access.

## Other Common Settings

These are starting points for a local Compose project, not a list of changes
you need to make:

| Setting | Use it to… | Next step |
| --- | --- | --- |
| `server.host` | Choose the host interface that accepts connections. `127.0.0.1` is local-only; `0.0.0.0` listens on all IPv4 interfaces. | Review [Security](security.md) before widening exposure, then deploy. |
| `server.port` | Choose the SFTP port exposed on that host. | Follow the exercise above. |
| `auth.allow_password` | Allow or disable password authentication. | Verify working key access before disabling passwords; follow [Key-Only Deployments](security.md#key-only-deployments). |
| `sync.interval_seconds` | Set the interval between automatic user checks; defaults to 60 seconds with synchronization enabled. | Read [Automatic User Synchronization](operations.md#automatic-user-synchronization), then deploy. |

Individual user accounts and writable upload directories belong to the provider,
not to these basic server settings. Manage them with the
[user commands](cli/users.md).

The remaining sections provide the detailed field reference and advanced
configuration behavior. Before changing storage paths, provider mounts, server
identity or deployment targets, read [Recovery and upgrades](recovery.md).

## Project Config

Minimum valid config:

```yaml
version: 1
project:
  name: sftpwarden
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
  user_schema: 2
```

Common runtime settings:

```yaml
server:
  host: "0.0.0.0"
  port: 2222
  data_dir: /data
  host_keys_dir: /etc/sftpwarden/host_keys
  state_dir: /var/lib/sftpwarden

sync:
  interval_seconds: 60
  apply_on_startup: true
  disable_missing_users: true

auth:
  allow_public_key: true
  allow_password: true
  recommended: password

healthcheck:
  interval_seconds: 30
  timeout_seconds: 10
  retries: 3
  start_period_seconds: 20
```

`server.container_port` is not supported. The container SSH port is always `22`;
`server.port` controls the host port exposed by Docker Compose.

`healthcheck` controls the generated Docker Compose container healthcheck timing.
The healthcheck command itself stays `sftpwarden runtime health` inside the
runtime container.

Read a project setting by omitting its value. Add a value to save a change:

The following are independent examples, not a sequence to apply:

```bash
sftpwarden config project.name
sftpwarden config project.name prod2
sftpwarden config server.port 2200
```

`sftpwarden config --help` lists the most common dotted paths as individual
commands. The generic `sftpwarden config PATH [VALUE]` form also works for valid
paths that follow the YAML structure.

## Configuration Lifecycle

`sftpwarden.yaml` is the desired project configuration. Updating it with
`sftpwarden config PATH VALUE` and editing it by hand are equivalent from an
operations point of view: both persist the desired state, but neither one
changes the running runtime or rewrites deployment artifacts by itself.

After changing `sftpwarden.yaml`, use the deploy command for the active
deployment path:

```bash
sftpwarden plan
sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

For direct Kubernetes or Helm workflows, the deploy-equivalent commands are
`sftpwarden kube apply` and `sftpwarden helm upgrade --install`.

Deploy reconciles project metadata and generated deployment files before it
applies the runtime. For example, changing `project.name` in YAML updates the
registered context name, changing `server.port` rewrites and reapplies
`docker-compose.yml` and changing Kubernetes probe or PVC settings rewrites the
rendered manifests or Helm values.

`refresh` is intentionally narrower: it asks a running runtime to reload users
that are already visible through the configured provider. `watch` is only for
syncing editable user provider files in remote `local-sync` contexts. Neither
command applies `sftpwarden.yaml` changes.

### Applied settings and persistence

The runtime validates a candidate OpenSSH configuration at first container start
and keeps a private snapshot of the deployed settings in its container layer.
User refresh, periodic sync and health use that snapshot. Restarting the same
Compose container retains it. Local and remote `local-sync` Compose `deploy`
recreate the container, validate the new daemon configuration and wait for
readiness. Remote-only deploy prepares the remote project in place using the
matching CLI release's preparation image before recreating the runtime.
This also applies to environment-based provider credentials: recreate through deploy to activate a
secret change.

Manual refresh rejects a provider-setting change until deploy and reports other
pending configuration changes without activating them. The background worker
continues reading the deployed provider even if the desired config becomes
invalid. Fix the desired file before the next deploy. Do not edit or remove the
runtime's private applied snapshot to activate changes.

Compose mounts the project's `data/`, `state/` and `host_keys/` directories at
the configured `server.data_dir`, `server.state_dir` and `server.host_keys_dir`
container paths. Changing those destinations requires deploy; their host-side
project directories remain the same.

### Schema changes

`provider.user_schema` follows the same lifecycle. If the config command detects
that changing it will require a provider data migration, it asks before accepting
the config change. The migration itself runs during the next deploy,
`kube apply` or `helm upgrade`, with confirmation unless `--yes` is used.
Dry-runs show the planned migration without writing provider data. Manual YAML
edits are detected later by health and deploy commands; unsupported schema
versions or backward schema changes fail with a clear configuration/provider
error.

## Runtime Settings

The project config is organized into focused sections:

| Section | Purpose |
| --- | --- |
| `server` | Host bind address, exposed SFTP port, container data paths and Linux group. |
| `sync` | Runtime refresh behavior, periodic reconciliation and missing-user handling. |
| `auth` | Public-key/password enablement and password hash policy. |
| `isolation` | Chroot mode, upload directory, ownership and directory permissions. |
| `uid_gid` | Automatic UID/GID allocation range and whether existing IDs are preserved. |
| `provider` | User provider type, path/DSN/table/collection and user schema version. |
| `logging` | Runtime log level and format. |
| `healthcheck` | Docker Compose container healthcheck timings. |
| `docker` | Generated image, container name, restart policy and compose file name. |
| `deploy` | Deployment target: Compose or Kubernetes. |
| `kubernetes` | Manifest/Helm namespace, release, storage, probes and replica settings. |
| `remote` | Remote deployment host, SSH, storage mode and sync behavior. |
| `watcher` | Local-sync watcher backend, image and enablement. |

Use `sftpwarden deploy` after changing any section that affects generated files,
remote deployment or runtime container settings.

### Project Variables

These are the supported dotted paths for `sftpwarden.yaml`. You can read or
change them with `sftpwarden config <path> [value]` or edit the YAML directly
and apply it with deploy.

Project metadata:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `version` | Project config file format version. Current value is `1`. | Do not change manually unless a release explicitly introduces a new config format. |
| `project.name` | Human and CLI name for the environment. Registered contexts use this name when reconciled. | Change when renaming the environment; deploy reconciles the registered context name. |
| `project.description` | Free-form project description. | Change for operator clarity, inventory or documentation. It does not affect runtime access. |

Runtime server and sync behavior:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `server.host` | Host interface exposed by generated Docker Compose port bindings. Default `0.0.0.0`. | Use `127.0.0.1` for local-only development or another interface when the host should bind narrowly. |
| `server.port` | Host-facing SFTP port for Compose deployments. The container still listens on `22`. Default `2222`. | Change when the host port conflicts or when exposing SFTP on a chosen port. Requires deploy. |
| `server.data_dir` | Container path for SFTP user data. Default `/data`. | Change only with matching volume/deployment changes; this is where user files live inside the runtime. |
| `server.host_keys_dir` | Container path for persisted OpenSSH host keys. Default `/etc/sftpwarden/host_keys`. | Change only when customizing runtime mounts; losing this path changes server fingerprints. |
| `server.state_dir` | Container path for runtime state such as UID/GID allocation state. Default `/var/lib/sftpwarden`. | Change only when customizing runtime mounts; losing state can change allocated IDs. |
| `server.group` | Linux group assigned to managed SFTP users. Default `sftpwarden_users`. | Change when your container policy requires a different group name. |

#### Synchronization

| Field | Purpose | When to change it |
| --- | --- | --- |
| `sync.enabled` | Enables the long-running runtime sync loop. The entrypoint still performs one startup refresh before starting OpenSSH. | Disable only when you want manual refreshes after startup and understand users will not be reconciled periodically. |
| `sync.interval_seconds` | Periodic interval between runtime reconciliation starts. Minimum 5 seconds; default 60. File-provider metadata changes can wake the loop earlier; config edits cannot. Periodic passes still enforce expiry. Passes never overlap and slow work does not add another full interval of waiting. | Lower for faster database reconciliation, raise to reduce periodic provider reads. |
| `sync.apply_on_startup` | Accepted startup refresh policy field. Current runtime entrypoint always performs startup refresh. | Leave at the default unless future release notes describe changed startup behavior. |
| `sync.disable_missing_users` | Disables Linux/runtime users that exist in state but no longer exist in the provider. | Keep enabled for declarative providers. Disable only if removed provider entries should not disable runtime users. |
| `sync.delete_missing_user_data` | Accepted safety field for future missing-user data deletion policy. Current runtime does not delete user data automatically. | Leave disabled. Use explicit user deletion flags for destructive data removal. |

Authentication, isolation and UID/GID allocation:

| Field | Purpose | When to change it |
| --- | --- | --- |

#### Authentication

| Field | Purpose | When to change it |
| --- | --- | --- |
| `auth.allow_public_key` | Enables public-key authentication in generated `sshd_config` and runtime user validation. | Disable only for password-only environments. |
| `auth.allow_password` | Enables password authentication in generated `sshd_config` and runtime user validation. | Disable for key-only production profiles. At least one auth method must stay enabled. |
| `auth.recommended` | Operator-facing preference for which auth style the project recommends. Runtime behavior is controlled by `allow_public_key` and `allow_password`. | Set to `public_key` or `password` to document the intended onboarding style. |
| `auth.password_hash_scheme` | Accepted password hash scheme setting. Current accepted value is `sha512crypt`. | Keep `sha512crypt`; plaintext provider passwords are rejected. |

#### Isolation and account identity

| Field | Purpose | When to change it |
| --- | --- | --- |
| `isolation.mode` | Runtime isolation mode. Current supported value is `chroot`. | Do not change; other modes are not supported. |
| `isolation.upload_dir` | Reserved compatibility field; does not override provider users. | Set each user's `upload_dir` through the provider or user command to select a writable directory. |
| `isolation.root_owner` | Chroot roots are owned by `root`. Other values are rejected. | Keep `root`; non-root chroot ownership is unsupported. |
| `isolation.root_group` | Chroot roots use group `root`. Other values are rejected. | Keep `root`; changing this field cannot change directory ownership. |
| `isolation.root_permissions` | Permissions for chroot roots. Default `755`; must not be group/other writable. | Change only with OpenSSH chroot rules in mind. Unsafe values are rejected. |
| `isolation.upload_permissions` | Permissions for writable upload directories. Default `750`; must not be world writable. | Change when group access policy requires it, while keeping the directory safe. |
| `uid_gid.mode` | UID/GID allocation strategy. Current supported value is `auto`. | Do not change; manual modes are not implemented. |
| `uid_gid.start` | First automatically allocated UID/GID. Default `10000`. | Change to avoid collisions with existing users inside the runtime image or host-mounted files. |
| `uid_gid.end` | Highest automatically allocated UID/GID. Default `60000`. | Change when you need a wider or narrower allocation range. Must be greater than `start`. |
| `uid_gid.preserve_existing` | Accepted compatibility field. Runtime currently preserves allocations through state for existing users. | Leave enabled; preserve the `state/` volume to keep IDs stable. |

Provider, logging, healthcheck and Compose rendering:

| Field | Purpose | When to change it |
| --- | --- | --- |

#### Provider

| Field | Purpose | When to change it |
| --- | --- | --- |
| `provider.type` | User provider backend: `yaml`, `csv`, `sqlite`, `mysql`, `mariadb`, `postgresql` or `mongodb`. | Change when moving user storage to a different backend. Use transfer/migration workflows for existing users. |
| `provider.path` | YAML/CSV/SQLite provider path or container provider path. For default file providers, `/etc/sftpwarden/users.*` maps to the local project provider file. External database providers use `dsn`, `table` or `collection` instead. | Change when using a different local provider filename or mounted path. |
| `provider.dsn` | Database DSN for MySQL, MariaDB, PostgreSQL or MongoDB. Environment variable references are supported. | Set for database-backed providers. Prefer environment variables for secrets. |
| `provider.query` | Optional custom read-only SQL query for MySQL, MariaDB or PostgreSQL. | Use only for integrating with an existing SQL user table shape. Mutations require the normal table layout. |
| `provider.table` | SQL users table name for SQLite, MySQL, MariaDB and PostgreSQL. Default `sftp_users`. | Change when your database uses a different table name. |
| `provider.collection` | MongoDB collection name. Default `sftp_users`. | Change when using a different MongoDB collection. |
| `provider.user_schema` | Desired user schema version. New init projects default to `2`; older configs that omit it load as v1. | Use `1` for simple `public_keys`, `2` for named keys. Forward migration is applied by deploy-equivalent commands. |

#### Logging and health

| Field | Purpose | When to change it |
| --- | --- | --- |
| `logging.level` | Runtime log level: `debug`, `info`, `warning` or `error`. | Raise to debug troubleshooting; lower for quieter production logs. |
| `logging.format` | Runtime log format: `json` or `text`. Default `json`. | Use `json` for log pipelines and `text` for human local debugging. |
| `healthcheck.interval_seconds` | Docker Compose healthcheck interval. | Change when Compose should check runtime health more or less often. |
| `healthcheck.timeout_seconds` | Docker Compose healthcheck command timeout. | Increase for slow hosts or storage; lower for quicker failure detection. |
| `healthcheck.retries` | Consecutive Compose healthcheck failures before unhealthy. | Increase to tolerate slow startup or transient provider latency. |
| `healthcheck.start_period_seconds` | Compose grace period before failures count. | Increase when runtime startup is expected to take longer. |

#### Deployment and Kubernetes

| Field | Purpose | When to change it |
| --- | --- | --- |
| `docker.image` | Runtime image reference in generated Compose. Source checkouts default to local build behavior. | Change to use a published or private runtime image. |
| `docker.container_name` | Compose container name for the runtime service. | Change when multiple local contexts run on the same Docker host. |
| `docker.restart` | Compose restart policy. Default `unless-stopped`. | Change to match host operations policy, for example `always` in a lab/production host. |
| `docker.compose_file` | Generated Compose filename. Default `docker-compose.yml`. | Local deploy, refresh and health use this filename. Remote operations use the context's `remote.compose_file`. |
| `deploy.target` | Deployment target: `compose` or `kubernetes`. | Change when switching a project from Compose to Kubernetes workflows. |

Kubernetes and Helm:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `kubernetes.mode` | Kubernetes deployment mode: `manifests` for `kubectl`, `helm` for Helm. | Change to choose direct manifests or Helm-managed release workflows. |
| `kubernetes.host_keys_secret` | Existing SSH identity Secret; null selects a retained generated-key PVC. | Prepare and verify identity before switching authority or upgrading legacy Pods. |
| `kubernetes.provider_dsn_secret` | Existing Secret with `SFTPWARDEN_PROVIDER_DSN`; null selects private managed delivery. | Set for operator-managed credentials; do not put resolved credentials in public values. |
| `kubernetes.namespace` | Kubernetes namespace used by manifests and Helm. Default `sftpwarden`. | Change to match cluster tenancy or environment naming. |
| `kubernetes.release` | Helm release name and Kubernetes app naming seed. Defaults to the project name during init. | Change when the Helm release should have a different name from the project. |
| `kubernetes.kube_context` | Optional kube context passed to `kubectl` or Helm. | Set when your kubeconfig has multiple clusters and SFTPWarden must target one explicitly. |
| `kubernetes.service_type` | Service type: `ClusterIP`, `NodePort` or `LoadBalancer`. | Change when the cluster exposure model requires node ports or cloud load balancers. |
| `kubernetes.storage_class` | Optional StorageClass for generated PVCs. `null` uses the cluster default. | Set when the cluster requires a specific storage class. |
| `kubernetes.data_storage_size` | PVC size for SFTP user data. Default `10Gi`. | Increase before deploy when users need more upload storage. Kubernetes does not shrink PVCs. |
| `kubernetes.startup_probe.period_seconds` | Startup probe period. | Tune when runtime startup checks should run more or less often. |
| `kubernetes.startup_probe.timeout_seconds` | Startup probe timeout. | Increase when startup health checks are slow. |
| `kubernetes.startup_probe.failure_threshold` | Startup failures allowed before Kubernetes considers startup failed. Default `30`. | Increase for slow first boot, large provider startup or slow storage. |
| `kubernetes.readiness_probe.period_seconds` | Readiness probe period. | Tune how quickly Kubernetes notices whether the pod should receive traffic. |
| `kubernetes.readiness_probe.timeout_seconds` | Readiness probe timeout. | Increase for slow health commands. |
| `kubernetes.readiness_probe.failure_threshold` | Readiness failures allowed before pod is marked not ready. | Increase when transient provider checks are expected. |
| `kubernetes.liveness_probe.period_seconds` | Liveness probe period. | Tune how often Kubernetes checks whether the runtime should be restarted. |
| `kubernetes.liveness_probe.timeout_seconds` | Liveness probe timeout. | Increase for slow hosts or storage. |
| `kubernetes.liveness_probe.failure_threshold` | Liveness failures allowed before restart. | Increase to avoid restarts from brief provider or filesystem stalls. |
| `kubernetes.replicas` | Runtime replica count. Current supported value is `1`. | Keep `1`; multi-pod runtime support is not implemented yet. |

Remote project defaults and watcher preferences:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `remote.enabled` | Accepted project-level marker for remote deployment intent. Actual command routing comes from the context registry. | Usually leave managed by init/context workflows; context entries decide whether a command is local or remote. |
| `remote.storage` | Project-level remote storage preference: `local-sync` or `remote-only`. Context `storage` is the operational source of truth. | Change through context commands when changing how an environment is managed. |
| `remote.host` | Project-level remote host hint. Context `remote.host` controls real SSH commands. | Prefer updating the context registry with `sftpwarden context host`. |
| `remote.user` | Project-level remote SSH user hint. Context `remote.user` controls real SSH commands. | Prefer updating the context registry with `sftpwarden context user`. |
| `remote.port` | Project-level SSH port hint. Context `remote.port` controls real SSH commands. | Prefer updating the context registry with `sftpwarden context port`. |
| `remote.remote_root` | Project-level remote root hint. Context `remote.remote_root` controls real remote paths. | Prefer updating the context registry with `sftpwarden context remote-root`. |
| `remote.remote_config` | Project-level remote config path hint. Context `remote.remote_config` controls remote-only config discovery. | Prefer updating the context registry with `sftpwarden context remote-config`. |
| `remote.ssh_key` | Project-level SSH key hint. Context `remote.ssh_key` controls actual SSH key usage. | Prefer updating the context registry with `sftpwarden context ssh-key`. |
| `remote.delete_extra_files` | Accepted remote sync policy field. Current deploy sync does not delete extra remote files automatically. | Leave disabled unless a future release documents active deletion behavior. |
| `remote.include_env` | Explicitly delivers the local `.env` privately during remote local-sync deploy. Watcher and ordinary sync exclude it. | Enable only when the selected local file should become the remote deployment environment. |
| `watcher.enabled` | Project-level watcher preference. Global watcher installation and context `watcher_required` drive actual watcher behavior. | Use `sftpwarden watcher install/status/uninstall` for real watcher management. |
| `watcher.mode` | Preferred watcher backend for this project. Supported values match global `defaults.watcher_mode`. | Change when this context needs a specific watcher backend. |
| `watcher.image` | Docker watcher image when `watcher.mode` is `docker`. | Set only for Docker watcher deployments using a custom image. |

## Deploy Target

For Docker Compose, `server.host` selects the host interface used to publish the
SFTP port. Use `127.0.0.1` or `::1` for loopback-only access; `0.0.0.0` or `::`
selects the corresponding wildcard interface. Compose uses long port syntax so
IPv6 addresses are unambiguous. The container's OpenSSH listener remains on port
22. Apply binding changes with `deploy`, then inspect `docker compose port
sftpwarden 22` on the Docker host.

`kubernetes.host_keys_secret` selects an existing Secret containing SSH key pairs;
otherwise identity uses a retained PVC. `kubernetes.provider_dsn_secret` selects
an existing provider Secret with key `SFTPWARDEN_PROVIDER_DSN`; otherwise CLI
preparation creates a private managed Secret from the explicitly resolved DSN.
Rendering alone does not verify these resources or credentials.

Compose is the default deployment target:

```yaml
deploy:
  target: compose
```

Kubernetes manifests:

```yaml
deploy:
  target: kubernetes
kubernetes:
  mode: manifests
  namespace: sftpwarden
  release: sftpwarden
  kube_context: null
  service_type: ClusterIP
  storage_class: null
  data_storage_size: 10Gi
  startup_probe:
    failure_threshold: 30
    period_seconds: 5
    timeout_seconds: 5
  readiness_probe:
    failure_threshold: 3
    period_seconds: 10
    timeout_seconds: 5
  liveness_probe:
    failure_threshold: 3
    period_seconds: 30
    timeout_seconds: 5
  replicas: 1
```

Helm:

```yaml
deploy:
  target: kubernetes
kubernetes:
  mode: helm
```

Generated Helm values include `runtime.replicas: 1`, the rendered
`sftpwarden.yaml`, PVC defaults for data/state/provider storage and
`provider.bootstrapContent`. For YAML/CSV providers generated from a local
SFTPWarden project, `provider.bootstrapContent` comes from the local provider
file and is copied into the provider PVC on each Helm rollout. Because rendered
values include those provider entries, review and store them with the same care
as other deployment material. The default Kubernetes namespace is `sftpwarden`.

`kubernetes.namespace` is used by both manifest and Helm projects. During
`sftpwarden init --deploy kube` and `sftpwarden init --deploy helm`, the CLI
checks whether that namespace exists. The default namespace is `sftpwarden` and
`--yes` creates it automatically when it is missing. Pass `--namespace <name>` to
select a different existing or new namespace. Interactive init asks before
creating a missing namespace; `--no-create-namespace` requires the selected
namespace to already exist.

Use `kubernetes.kube_context` when the CLI should pass an explicit kube context
to `kubectl` or Helm. The generated Helm values use the Helm-style key
`kubernetes.kubeContext`.

`kubernetes.service_type` accepts `ClusterIP`, `NodePort` or `LoadBalancer`.
`kubernetes.storage_class` can stay `null` to use the cluster default storage
class or be set to a named StorageClass.

`kubernetes.data_storage_size` controls the SFTP user data PVC, where uploaded
files live. It defaults to `10Gi` and is rendered as `persistence.data.size` in
generated Helm values.

`kubernetes.startup_probe`, `kubernetes.readiness_probe` and
`kubernetes.liveness_probe` control the generated Kubernetes runtime health
probes. They are rendered directly into `kubernetes.yml` and as `probes.startup`,
`probes.readiness` and `probes.liveness` in generated Helm values.

`kubernetes.replicas` is reserved for future multi-node support. SFTPWarden v1.3.1
accepts only `1`; higher values fail with an explanation because multi-pod
runtime support requires shared storage, shared host keys, provider-safe refresh
and UID/GID consistency.

The same values can be changed through the CLI:

```bash
sftpwarden config deploy.target kubernetes
sftpwarden config kubernetes.mode helm
sftpwarden config kubernetes.namespace sftpwarden
sftpwarden config kubernetes.kube_context kind-sftpwarden
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden config kubernetes.startup_probe.failure_threshold 60
sftpwarden config kubernetes.liveness_probe.period_seconds 45
sftpwarden config kubernetes.replicas 1
```

## Providers

The provider section selects where accounts are stored. It does not select the
location of uploaded SFTP files.

| Setting | Use |
| --- | --- |
| `provider.type` | Choose YAML, CSV, SQLite or a supported database. |
| `provider.path` | Container path for a file provider. Compose deploy updates it when migrating to the dedicated provider directory. |
| `provider.dsn` | Database address or an environment reference such as `${SFTPWARDEN_POSTGRES_DSN}`. |
| `provider.table` / `provider.collection` | Select database storage; changing the name does not move existing accounts. |
| `provider.user_schema` | Desired user format. New projects use schema v2 with named keys. |

Use [Providers](providers.md#provider-configuration) for complete configuration
examples, optional dependencies and database credentials. That guide also owns
the [file formats](providers.md#file-providers),
[SQL schema](providers.md#sql-providers) and
[MongoDB requirements](providers.md#mongodb).

Choose another provider with `init --provider TYPE` for a new project.
To move accounts from an existing provider, use
[provider transfer](providers.md#transfer-and-migration) before activating the
destination. Changing `provider.type` alone does not copy accounts.

For Kubernetes YAML/CSV providers, deploy delivers the local provider file.
Database providers can update independently through refresh or the enabled sync
loop. SQLite is intended for single-host use; Kubernetes does not bootstrap its
PVC from a local SQLite file.

## Global CLI Config

Global defaults affect future initialization and watcher selection. They do not
change the settings of an existing running project. This is the global TOML
format, separate from project YAML:

```toml
version = 1
default_provider = "yaml"

[defaults]
root = "~/sftpwarden"
remote_root = "~/sftpwarden"
ssh_port = 22
remote_storage = "local-sync"
watcher_mode = "auto"
sync_interval_seconds = 60

[updates]
check_enabled = true
```

`watcher_mode` controls the default backend used when a remote `local-sync`
context needs a watcher and no `--watcher` option is passed. The default `auto`
detects the host scheduler. Explicit values are `systemd`, `openrc`, `runit`,
`supervisord`, `launchd`, `windows-task` and `docker`.

Provider selection order:

1. `--provider`
2. `SFTPWARDEN_DEFAULT_PROVIDER`
3. `default_provider` from the global config
4. built-in fallback `yaml`

### Global Variables

`updates.check_enabled` enables advisory release notices after selected interactive
commands. Set it to `false`, or set `SFTPWARDEN_NO_UPDATE_CHECK=1`, to disable them.
Explicit `upgrade` checks remain available. Release caches and update receipts live
in the private `updates/` directory under `SFTPWARDEN_HOME`; watcher installation
and process receipts are operational state, not project configuration to deploy.

Global config lives in `~/.sftpwarden/config.toml` by default. Set
`SFTPWARDEN_HOME` when tests, CI or isolated operator workstations need a
different global config and context registry directory.

### Concurrent global updates

CLI context changes and global default/watcher changes lock the complete
read-modify-write operation and publish complete TOML files atomically. A competing
operation waits up to five seconds before reporting a retryable lock error.
Sibling lock files remain in place between operations; their presence alone does
not mean a process is running. External editors do not participate in these locks,
so avoid editing the same state while a CLI operation is in progress.

Prefer CLI commands for normal changes:

```bash
sftpwarden config show
sftpwarden config default-provider yaml
```

| Field | Purpose | When to change it |
| --- | --- | --- |
| `version` | Global config file format version. Current value is `1`. | Do not change manually unless release notes say a new global config format exists. |
| `default_provider` | Provider used by `sftpwarden init` when `--provider` and `SFTPWARDEN_DEFAULT_PROVIDER` are not set. | Change when most new projects on this machine should start with `yaml`, `csv`, `sqlite`, `mysql`, `mariadb`, `postgresql` or `mongodb`. |
| `defaults.root` | Accepted default local root field. Current local `init` uses the current directory unless `--root` is passed. | Leave as-is unless a future release documents active use of this default. |
| `defaults.remote_root` | Default remote project directory used by remote init/context commands when the remote URL does not include a path. | Change to match your server convention, for example `/opt/sftpwarden`. |
| `defaults.ssh_port` | Default SSH port for remote contexts. | Change when your remote deployment hosts normally use a non-22 SSH port. |
| `defaults.remote_storage` | Accepted remote storage default field. Current remote init chooses `local-sync` unless `--remote-only` is passed. | Leave as-is unless a future release documents active use of this default. |
| `defaults.watcher_mode` | Default watcher backend for remote `local-sync` contexts. Supported values are `auto`, `systemd`, `openrc`, `runit`, `supervisord`, `launchd`, `windows-task` and `docker`. | Change when operations policy requires a specific scheduler instead of auto-detection. |
| `defaults.sync_interval_seconds` | Accepted global sync interval field. Current watcher and runtime sync intervals are configured by command/project settings instead. | Leave as-is unless a future release documents active use of this default. |
| `watcher.installed` | Records whether SFTPWarden believes a watcher is installed for this global home. | Managed by `sftpwarden watcher install/uninstall`; do not edit by hand except for recovery from broken local state. |
| `watcher.mode` | Records the installed watcher backend. | Managed by watcher commands. Change by reinstalling the watcher with `--watcher`. |
| `watcher.managed_by` | Marker that the watcher state belongs to SFTPWarden. | Internal safety metadata; do not change. |
| `watcher.path` | Path to the generated watcher service/task/compose file. | Managed by watcher commands. Useful for troubleshooting where the installed watcher lives. |
| `watcher.activated` | Records whether the watcher was activated after installation. | Managed by watcher commands. Use `watcher status` instead of editing it manually. |

## Contexts

Local context:

```toml
[contexts.dev]
name = "dev"
type = "local"
root = "/Users/example/sftpwarden-dev"
config = "/Users/example/sftpwarden-dev/sftpwarden.yaml"
provider = "yaml"
critical = false
```

Remote local-sync context:

```toml
[contexts.prod]
name = "prod"
type = "remote"
storage = "local-sync"
root = "/Users/example/sftpwarden-prod"
config = "/Users/example/sftpwarden-prod/sftpwarden.yaml"
provider = "yaml"
critical = true
watcher_required = true

[contexts.prod.remote]
host = "sftp-prod.example.com"
user = "deploy"
port = 22
remote_root = "/opt/sftpwarden"
remote_config = "/opt/sftpwarden/sftpwarden.yaml"
compose_file = "docker-compose.yml"
```

Remote-only contexts keep top-level `root` and `config` empty because the source of
truth is already on the remote server.

### Context Registry Variables

Contexts live in `~/.sftpwarden/contexts.toml` by default or under
`$SFTPWARDEN_HOME/contexts.toml` when `SFTPWARDEN_HOME` is set. They are local
CLI routing metadata: they tell SFTPWarden which project to use, where it lives
and how to reach it. They are not deployed into the runtime container.

Use context commands for normal changes:

```bash
sftpwarden context ls
sftpwarden context show
sftpwarden context use prod
sftpwarden context remote-root /opt/sftpwarden-prod --yes
```

Registry-level fields:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `default` | Name of the active context used when no `--context` is passed. | Use `sftpwarden context use <name>` or `context default <name>` when switching environments. |
| `contexts` | Mapping of context name to context entry. | Managed by `init`, `context add`, `context rename` and `context remove`. |

Per-context fields:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `contexts.<name>.name` | Context name stored inside the entry. It should match the map key. | Rename with `sftpwarden context rename` or `sftpwarden context name`. |
| `contexts.<name>.type` | Context type: `local` or `remote`. | Change only when intentionally converting how the environment is managed. |
| `contexts.<name>.root` | Local project directory. For local and remote `local-sync` contexts, this is the local source folder. For remote-only contexts it is empty. | Change with `sftpwarden context root`; the CLI can copy files and update `config` safely. |
| `contexts.<name>.config` | Local path to `sftpwarden.yaml`. Empty for remote-only contexts. | Usually follows `root/sftpwarden.yaml`; change only for custom local layouts. |
| `contexts.<name>.provider` | Provider type recorded for quick context visibility and watcher planning. | Update when the project provider changes and the context registry should reflect it. |
| `contexts.<name>.critical` | Adds confirmation for production-sensitive operations such as deploy/remove. | Enable for production, live, customer or otherwise risky environments. |
| `contexts.<name>.storage` | Remote storage mode for remote contexts: `local-sync` or `remote-only`. `null` for local contexts. | Use `local-sync` when local project files are synced to a remote host; use `remote-only` when files already live only on the remote host. |
| `contexts.<name>.watcher_required` | Whether a remote `local-sync` context should be included in watcher planning. | Usually managed by remote init/context conversion. Disable only when that context must not be watched. |
| `contexts.<name>.remote` | Remote SSH endpoint and paths. Present only for remote contexts. | Managed by remote init and context field commands. |

Remote endpoint fields:

| Field | Purpose | When to change it |
| --- | --- | --- |
| `contexts.<name>.remote.host` | SSH host used for remote deploy, refresh, health, backup and cleanup checks. | Change when the deployment moves to another host. |
| `contexts.<name>.remote.user` | SSH username used for remote commands. | Change when operations should connect as a different deployment user. |
| `contexts.<name>.remote.port` | SSH port for remote commands. Default `22`. | Change when the server uses a non-standard SSH port. |
| `contexts.<name>.remote.remote_root` | Remote project directory. Remote Compose commands run from this directory. | Change when the project is moved on the remote host. The CLI does not move remote files automatically. |
| `contexts.<name>.remote.remote_config` | Remote path to `sftpwarden.yaml`, normally `<remote_root>/sftpwarden.yaml`. | Change only for custom remote layouts or remote-only contexts with a non-standard config path. |
| `contexts.<name>.remote.ssh_key` | Optional SSH private key path used by SFTPWarden SSH commands. | Set when this context should use a dedicated deployment key instead of the host default SSH config/agent. |
| `contexts.<name>.remote.compose_file` | Compose file name under `remote_root`. Default `docker-compose.yml`. | Change when the remote project uses a custom Compose filename. |

Context types behave differently:

| Context type | Required fields | What commands do |
| --- | --- | --- |
| Local | `type`, `root`, `config`, `provider` | Commands read and write local project files and run local Docker Compose or local render/apply steps. |
| Remote `local-sync` | Local fields plus `storage = "local-sync"` and `remote.*` | Deploy syncs required local files to `remote_root`, then runs remote Docker Compose over SSH. Watcher can sync editable provider files. |
| Remote-only | `type = "remote"`, `storage = "remote-only"`, `remote.*`; local `root` and `config` stay empty | Commands operate against the remote project in place. If the remote root disappears, SFTPWarden prunes only the local registry entry. |

You can inspect or update context registry values with `sftpwarden context`:

```bash
sftpwarden context show
sftpwarden context name prod2
sftpwarden context root ~/sftpwarden-prod2 --yes
sftpwarden context remote-root /opt/sftpwarden-prod --yes
```

Changing `root` through the CLI copies project files to the new folder and updates
the stored config path. Use `--delete-old-root` when you also want SFTPWarden to
remove the old folder after the copy.

Change context type only when you are intentionally changing how the environment
is managed:

```bash
sftpwarden context type remote --remote deploy@example.com:/opt/sftpwarden --yes
sftpwarden context type local --yes
```

Converting from remote to local removes remote metadata. Run without `--yes` if
you want an interactive confirmation.

## Validation

```bash
sftpwarden validate --config sftpwarden.yaml
sftpwarden validate --config sftpwarden.yaml --json
```

CI should validate every example config before release.

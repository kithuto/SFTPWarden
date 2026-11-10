# Configuration effects

`upgrade` normally changes only management tools. Server configuration and image
changes require deploy. If a replacement watcher is incompatible, upgrade asks
for explicit consent to deploy all contexts before changing anything; see
[Upgrade](cli/upgrade.md). Refresh and watcher delivery never perform that update.

Project values are stored in `sftpwarden.yaml`. Saving a value changes desired
configuration. **Deploy applies configuration; refresh applies provider users.**
Watcher delivery covers editable file providers in remote local-sync contexts.
A persisted value or rendered manifest alone does not prove an operational effect.

Use the activation column after changing a setting. For example, saving
`server.port` requires deploy followed by a connection on the new port.
Changing `auth.recommended` only changes metadata and cannot disable passwords.

A **compatibility field** is accepted in configuration but has no active effect
in 1.4.0. Follow the alternative stated in its row.

## Project settings

### Runtime and access policy

| Project field(s) | Owner / target | Activation and check |
| --- | --- | --- |
| `version` | Config parser | Validate; only format 1. |
| `project.name`, `project.description` | CLI metadata | Save and inspect; no authorization effect. |
| `server.host`, `server.port` | Compose publisher; port also selects cluster Service | Deploy; inspect published interface/port and connect. Cluster exposure uses Service networking, not `server.host`. |
| `server.data_dir`, `server.state_dir`, `server.host_keys_dir` | Runtime/deployment mounts | Deploy; inspect actual mounts and persistence. Changing a path does not migrate arbitrary external storage. |
| `server.group` | Linux account reconciliation | Deploy; inspect account group and file access. |
| `sync.enabled` | Runtime supervisor | Deploy; inspect worker process and health. Manual refresh remains explicit. |
| `sync.interval_seconds` | Runtime worker | Deploy; observe convergence and expiry without a provider edit. |
| `sync.disable_missing_users` | Runtime policy | Deploy config, then refresh/poll; check missing users are disabled when enabled. |
| `sync.delete_missing_user_data` | Compatibility field | Reserved; no automatic deletion. Use explicit user deletion safeguards. |
| `sync.apply_on_startup` | Compatibility field | Reserved; startup always reconciles before serving SSH. False cannot bypass policy. |
| `auth.allow_public_key`, `auth.allow_password` | SSH configuration/policy | Deploy; test allowed and denied access, including removal of the last credential. |
| `auth.recommended` | CLI recommendation | Metadata; does not enable/disable authentication. |
| `auth.password_hash_scheme` | Password validation | Only `sha512crypt`; plaintext is not provider input. |
| `isolation.mode`, `isolation.root_owner`, `isolation.root_group` | Chroot validation | Supported chroot/root ownership only; validate before deploy. |
| `isolation.upload_dir` | Compatibility field | Reserved; each provider user's `upload_dir` selects the actual writable directory. |
| `isolation.root_permissions`, `isolation.upload_permissions` | Runtime filesystem | Deploy; inspect ownership/modes and SFTP access. Unsafe links/modes are refused. |
| `uid_gid.mode` | Runtime allocator | Only `auto`. |
| `uid_gid.start`, `uid_gid.end` | Runtime allocator/state | Deploy; inspect assignments, conflicts and retained IDs after restart. |
| `uid_gid.preserve_existing` | Compatibility field | Reserved; managed IDs remain preserved regardless of this value. |

### Provider storage

| Project field(s) | Owner / target | Activation and check |
| --- | --- | --- |
| `provider.type`, `provider.path` | Registry and mounts | Deploy; inspect provider and actual directory mount. Legacy file mounts require migration. |
| `provider.dsn` | Provider/secret transport | Resolve references on the owning host; deploy transport changes and verify connection from the runtime. |
| `provider.query` | SQL reads | Read through selected provider; a custom query can make it read-only. Inspect capabilities before mutations. |
| `provider.table` | SQL table selection | Deploy runtime config; does not move old rows automatically. |
| `provider.collection` | MongoDB collection selection | Deploy runtime config; missing/replaced collection is an error, not empty policy. |
| `provider.user_schema` | Schema reconciliation | Explicit migration or deploy; verify schema and retained users/keys. Reads never migrate. |

### Logging and Compose

| Project field(s) | Owner / target | Activation and check |
| --- | --- | --- |
| `logging.level`, `logging.format` | Runtime/watcher events | Deploy runtime/restart watcher with changed config; inspect filtered text/JSON events. External stderr is not converted. |
| `healthcheck.interval_seconds`, `healthcheck.timeout_seconds`, `healthcheck.retries`, `healthcheck.start_period_seconds` | Compose healthcheck | Deploy; inspect actual health configuration and failure behavior. |
| `docker.image` | Compose/cluster image selection | Deploy; inspect running digest/version. Cluster nodes need a published or explicitly loaded image. |
| `docker.container_name`, `docker.restart`, `docker.compose_file` | Compose renderer/runner | Deploy; inspect container identity, restart policy and selected file. |

### Kubernetes and deployment targets

| Project field(s) | Owner / target | Activation and check |
| --- | --- | --- |
| `deploy.target` | Deployment workflow | Deploy selects Compose/Kubernetes; it does not migrate existing data between targets. |
| `kubernetes.mode` | Manifest/Helm workflow | Deploy selects resource application mechanism. |
| `kubernetes.namespace`, `kubernetes.release`, `kubernetes.kube_context` | Cluster selection | Deploy; inspect cluster/resource identities. Renaming does not migrate claims. |
| `kubernetes.service_type` | Service | Deploy; inspect Service and network access. |
| `kubernetes.storage_class`, `kubernetes.data_storage_size` | PVC requests | Deploy; inspect bound claims/capacity. StorageClass controls expansion; shrinking is unsupported. |
| `kubernetes.host_keys_secret` | Operator identity | Prepare then deploy; validate key pairs and runtime fingerprints. Otherwise identity uses a retained PVC. |
| `kubernetes.provider_dsn_secret` | Operator provider Secret | Prepare/deploy; verify DSN key and runtime connection. Preserve operator ownership. |
| `kubernetes.startup_probe.period_seconds`, `kubernetes.startup_probe.timeout_seconds`, `kubernetes.startup_probe.failure_threshold` | Startup probe | Deploy; inspect probe and startup failure behavior. |
| `kubernetes.readiness_probe.period_seconds`, `kubernetes.readiness_probe.timeout_seconds`, `kubernetes.readiness_probe.failure_threshold` | Readiness probe | Deploy; inspect readiness on provider/worker failure. |
| `kubernetes.liveness_probe.period_seconds`, `kubernetes.liveness_probe.timeout_seconds`, `kubernetes.liveness_probe.failure_threshold` | Liveness probe | Deploy; inspect required-process failure/restart. |
| `kubernetes.replicas` | Single-runtime constraint | Only 1; additional replicas are rejected. |

### Remote projects and watcher

| Project field(s) | Owner / target | Activation and check |
| --- | --- | --- |
| `remote.enabled`, `remote.storage` | Remote metadata/context creation | Register intended context, then deploy; existing context registry selects actual target. |
| `remote.host`, `remote.user`, `remote.port`, `remote.remote_root`, `remote.remote_config`, `remote.ssh_key` | Remote metadata | Context registration/update selects endpoint; inspect registry and dry-run. Project edits alone do not move an existing installation. |
| `remote.include_env` | Remote local-sync deploy | Explicit deploy privately copies local UTF-8 `.env` when true. Excluded from watcher and ordinary sync. False does not delete an existing remote file. |
| `remote.delete_extra_files` | Compatibility field | Reserved; no automatic deletion of unrelated remote files. |
| `watcher.enabled`, `watcher.mode`, `watcher.image` | Watcher setup | Install/update explicitly; inspect backend and actual delivery. Project config edits are not watched. |

## Global defaults and contexts

Global defaults in `~/.sftpwarden/config.toml` affect subsequent initialization
and backend selection; they do not redeploy existing projects. Registry fields
in `~/.sftpwarden/contexts.toml` select roots/endpoints, critical-operation
confirmation and watcher eligibility. See [Configuration](configuration.md) for
individual fields; inspect `context show` and the selected deploy target after
changes.

## Verify the running result

Run `sftpwarden health` after deployment and test the affected behavior, such as
login or file transfer. Rendered files and successful validation alone do not
prove that a running server uses the new settings.

Runtime policy uses the applied configuration snapshot. Health checks required
processes, synchronization failures and pending deployment drift. Invalid/missing
provider input retains the last applied policy and reports failure.

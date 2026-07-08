# Config Commands

`sftpwarden config` reads and updates configuration. It has two separate scopes:

- `sftpwarden config PATH [VALUE]` edits the active project's `sftpwarden.yaml`.
- `sftpwarden config show` and `sftpwarden config default-provider` operate on
  global CLI settings under `~/.sftpwarden/config.toml`.

Changing `sftpwarden.yaml` records desired state only. Apply deployment-level
changes with `sftpwarden deploy`, `sftpwarden kube apply`, or `sftpwarden helm
upgrade`.

## `sftpwarden config`

Command group and dynamic project-config editor.

```bash
sftpwarden config --help
```

Group-level options are also accepted by the dynamic path commands.

### Group Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects the project context whose `sftpwarden.yaml` should be read or edited. |
| `--config` | `PATH` | none | Reads or edits a specific `sftpwarden.yaml` directly. |
| `--yes`, `-y` | flag | false | Accepts prompts for important config changes, currently including deferred provider schema migration. |

## `sftpwarden config PATH`

Reads or updates one dotted path in `sftpwarden.yaml`. If `VALUE` is omitted,
the command prints the current value. If `VALUE` is supplied, SFTPWarden parses
it with YAML scalar rules, validates the full config model, and writes the file.

```bash
sftpwarden config server.port
sftpwarden config server.port 2200
sftpwarden config provider.type csv
sftpwarden config provider.user_schema 2
sftpwarden config auth.allow_password false
sftpwarden config kubernetes.liveness_probe.period_seconds 45
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `PATH` | Yes | supported dotted path | Config path exposed by the CLI, such as `server.port` or `provider.user_schema`. |
| `VALUE` | No | YAML scalar text | Replacement value. Omit it to read the current value. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects the project context to edit. |
| `--config` | `PATH` | none | Edits a specific config file directly. |
| `--yes`, `-y` | flag | false | Accepts confirmation when changing `provider.user_schema` requires a forward migration on next deploy. |

### Value Parsing

Numbers become integers, `true`/`false` become booleans, and `null` clears
nullable fields. Existing string fields remain strings even if the value looks
like a number or boolean.

Changing `project.name` also renames the registered context when the command is
operating through the context registry. Changing `provider.user_schema` validates
the requested schema and, when SFTPWarden can see that provider data is older,
warns that the next deploy/apply/upgrade will run a forward migration.

### Supported Project Paths

The command paths below come from the real `PROJECT_CONFIG_PATHS` constant and
the `SFTPWardenConfig` model.

| Path | Accepted value |
| --- | --- |
| `version` | `1` |
| `project.name` | text |
| `project.description` | text |
| `server.host` | text, usually an address such as `0.0.0.0` |
| `server.port` | integer `1` to `65535` |
| `server.data_dir` | path text used inside the runtime |
| `server.host_keys_dir` | path text used inside the runtime |
| `server.state_dir` | path text used inside the runtime |
| `server.group` | text group name |
| `sync.enabled` | boolean |
| `sync.interval_seconds` | integer `>= 5` |
| `sync.apply_on_startup` | boolean |
| `sync.disable_missing_users` | boolean |
| `sync.delete_missing_user_data` | boolean |
| `auth.allow_public_key` | boolean |
| `auth.allow_password` | boolean |
| `auth.recommended` | `public_key` or `password` |
| `auth.password_hash_scheme` | `sha512crypt` |
| `isolation.mode` | `chroot` |
| `isolation.upload_dir` | safe relative path |
| `isolation.root_owner` | text owner name |
| `isolation.root_group` | text group name |
| `isolation.root_permissions` | octal permission text |
| `isolation.upload_permissions` | octal permission text |
| `uid_gid.mode` | `auto` |
| `uid_gid.start` | integer `>= 1000` |
| `uid_gid.end` | integer `>= 1001` and greater than `uid_gid.start` |
| `uid_gid.preserve_existing` | boolean |
| `provider.type` | `yaml`, `csv`, `sqlite`, `mysql`, `postgresql`, `mariadb`, or `mongodb` |
| `provider.path` | provider path text |
| `provider.dsn` | text DSN or `null` |
| `provider.query` | text SQL query or `null` |
| `provider.table` | SQL table name text accepted by the config validator |
| `provider.collection` | MongoDB collection name text |
| `provider.user_schema` | supported user schema version, currently `1` or `2` |
| `logging.level` | `debug`, `info`, `warning`, or `error` |
| `logging.format` | `json` or `text` |
| `healthcheck.interval_seconds` | integer `>= 1` |
| `healthcheck.timeout_seconds` | integer `>= 1` |
| `healthcheck.retries` | integer `>= 1` |
| `healthcheck.start_period_seconds` | integer `>= 0` |
| `docker.image` | text image reference |
| `docker.container_name` | text container name |
| `docker.restart` | text Docker restart policy |
| `docker.compose_file` | text Compose file name/path |
| `deploy.target` | `compose` or `kubernetes` |
| `kubernetes.mode` | `manifests` or `helm` |
| `kubernetes.namespace` | text namespace |
| `kubernetes.release` | text release name |
| `kubernetes.kube_context` | text kube context or `null` |
| `kubernetes.service_type` | `ClusterIP`, `NodePort`, or `LoadBalancer` |
| `kubernetes.storage_class` | text storage class or `null` |
| `kubernetes.data_storage_size` | Kubernetes storage quantity such as `10Gi` |
| `kubernetes.startup_probe.period_seconds` | integer `>= 1` |
| `kubernetes.startup_probe.timeout_seconds` | integer `>= 1` |
| `kubernetes.startup_probe.failure_threshold` | integer `>= 1` |
| `kubernetes.readiness_probe.period_seconds` | integer `>= 1` |
| `kubernetes.readiness_probe.timeout_seconds` | integer `>= 1` |
| `kubernetes.readiness_probe.failure_threshold` | integer `>= 1` |
| `kubernetes.liveness_probe.period_seconds` | integer `>= 1` |
| `kubernetes.liveness_probe.timeout_seconds` | integer `>= 1` |
| `kubernetes.liveness_probe.failure_threshold` | integer `>= 1` |
| `kubernetes.replicas` | `1`; values greater than `1` are rejected by the config model |
| `remote.enabled` | boolean |
| `remote.storage` | `local-sync` or `remote-only` |
| `remote.host` | text host or `null` |
| `remote.user` | text SSH user or `null` |
| `remote.port` | integer `1` to `65535` |
| `remote.remote_root` | text remote path or `null` |
| `remote.remote_config` | text remote config path or `null` |
| `remote.ssh_key` | text key path or `null` |
| `remote.delete_extra_files` | boolean |
| `remote.include_env` | boolean |
| `watcher.enabled` | boolean |
| `watcher.mode` | `auto`, `systemd`, `openrc`, `runit`, `supervisord`, `launchd`, `windows-task`, or `docker` |
| `watcher.image` | text Docker watcher image or `null`; valid only when watcher mode is Docker |

For detailed meaning of each field, see [Configuration](../configuration.md).

## `sftpwarden config show`

Shows global CLI configuration stored under `~/.sftpwarden/config.toml`.

```bash
sftpwarden config show
sftpwarden config show --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--json` | flag | false | Prints global config data as JSON. Without it, output is YAML-like text. |

### Effects

Read-only.

## `sftpwarden config default-provider`

Shows or updates the global default provider used by `sftpwarden init` when
`--provider` is omitted.

```bash
sftpwarden config default-provider
sftpwarden config default-provider yaml
sftpwarden config default-provider csv
sftpwarden config default-provider sqlite
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `provider` | No | `yaml`, `csv`, `sqlite`, `mysql`, `postgresql`, `mariadb`, or `mongodb` | New global default provider. Omit it to print the current default. |

### Options

No command-specific options.

### Effects

When a provider argument is supplied, writes the global CLI config.

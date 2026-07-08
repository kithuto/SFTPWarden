# Configuration

SFTPWarden has three configuration layers:

- global CLI defaults in `~/.sftpwarden/config.toml`;
- registered contexts in `~/.sftpwarden/contexts.toml`;
- project settings in `<project-root>/sftpwarden.yaml`.

## Global CLI Config

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
```

`watcher_mode` controls the default backend used when a remote `local-sync`
context needs a watcher and no `--watcher` option is passed. The default `auto`
detects the host scheduler. Explicit values are `systemd`, `openrc`, `runit`,
`supervisord`, `launchd`, `windows-task`, and `docker`.

Provider selection order:

1. `--provider`
2. `SFTPWARDEN_DEFAULT_PROVIDER`
3. `default_provider` from the global config
4. built-in fallback `yaml`

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

You can read or update any project setting with `sftpwarden config`:

```bash
sftpwarden config project.name
sftpwarden config project.name prod2
sftpwarden config server.port 2200
sftpwarden config auth.allow_password false
```

If you edit `sftpwarden.yaml` by hand, run `sftpwarden deploy` to apply that
configuration change. Deploy reconciles important metadata before it runs; for
example, changing `project.name` in YAML updates the registered context name.
`watch` and `refresh` only handle user changes.

## Deploy Target

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
`sftpwarden.yaml`, PVC defaults for data/state/provider storage, and
`provider.bootstrapContent`. For YAML/CSV providers generated from a local
SFTPWarden project, `provider.bootstrapContent` comes from the local provider
file and is copied into the provider PVC on each Helm rollout. Because rendered
values include those provider entries, review and store them with the same care
as other deployment material. The default Kubernetes namespace is `sftpwarden`.

`kubernetes.namespace` is used by both manifest and Helm projects. During
`sftpwarden init --deploy kube` and `sftpwarden init --deploy helm`, the CLI
checks whether that namespace exists. The default namespace is `sftpwarden`, and
`--yes` creates it automatically when it is missing. Pass `--namespace <name>` to
select a different existing or new namespace. Interactive init asks before
creating a missing namespace; `--no-create-namespace` requires the selected
namespace to already exist.

Use `kubernetes.kube_context` when the CLI should pass an explicit kube context
to `kubectl` or Helm. The generated Helm values use the Helm-style key
`kubernetes.kubeContext`.

`kubernetes.service_type` accepts `ClusterIP`, `NodePort`, or `LoadBalancer`.
`kubernetes.storage_class` can stay `null` to use the cluster default storage
class, or be set to a named StorageClass.

`kubernetes.data_storage_size` controls the SFTP user data PVC, where uploaded
files live. It defaults to `10Gi` and is rendered as `persistence.data.size` in
generated Helm values.

`kubernetes.startup_probe`, `kubernetes.readiness_probe`, and
`kubernetes.liveness_probe` control the generated Kubernetes runtime health
probes. They are rendered directly into `kubernetes.yml` and as `probes.startup`,
`probes.readiness`, and `probes.liveness` in generated Helm values.

`kubernetes.replicas` is reserved for future multi-node support. SFTPWarden v1.3
accepts only `1`; higher values fail with an explanation because multi-pod
runtime support requires shared storage, shared host keys, provider-safe refresh,
and UID/GID consistency.

The same values can be changed through the CLI:

```bash
sftpwarden config deploy.target kubernetes
sftpwarden config kubernetes.mode helm
sftpwarden config kubernetes.namespace sftpwarden
sftpwarden config kubernetes.kube_context kind-sftpwarden
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden config healthcheck.interval_seconds 45
sftpwarden config kubernetes.startup_probe.failure_threshold 60
sftpwarden config kubernetes.liveness_probe.period_seconds 45
sftpwarden config kubernetes.replicas 1
```

## Providers

YAML:

```yaml
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
  user_schema: 2
```

CSV:

```yaml
provider:
  type: csv
  path: /etc/sftpwarden/users.csv
  user_schema: 2
```

In Kubernetes manifest and Helm projects, YAML and CSV providers are treated as
declarative local files. `sftpwarden deploy`, `sftpwarden kube apply`, and
`sftpwarden helm upgrade --install` copy the rendered local provider contents
into the provider PVC during rollout. `sftpwarden refresh` reloads whatever is
already inside the runtime; it does not copy local YAML/CSV files into the
cluster. This is best suited to GitOps-style workflows where reviewed deploys are
the source of truth.

SQLite:

```yaml
provider:
  type: sqlite
  path: /etc/sftpwarden/users.sqlite
  user_schema: 2
```

SQLite uses Python's built-in `sqlite3` module and does not need an optional
dependency. It is useful for single-host deployments where you want a local
database file instead of YAML or CSV. Avoid SQLite on NFS, high-concurrency,
multi-writer deployments, and production Kubernetes. SQLite provider files are
not declaratively copied into Kubernetes PVCs.

MySQL:

```yaml
provider:
  type: mysql
  dsn: "${SFTPWARDEN_MYSQL_DSN}"
  table: sftp_users
  user_schema: 2
```

MariaDB:

```yaml
provider:
  type: mariadb
  dsn: "${SFTPWARDEN_MARIADB_DSN}"
  table: sftp_users
  user_schema: 2
```

PostgreSQL:

```yaml
provider:
  type: postgresql
  dsn: "${SFTPWARDEN_POSTGRES_DSN}"
  table: sftp_users
  user_schema: 2
```

MongoDB:

```yaml
provider:
  type: mongodb
  dsn: "${SFTPWARDEN_MONGODB_DSN}"
  collection: sftp_users
  user_schema: 2
```

The DSN follows the standard database URL convention:

```text
mysql://user:password@host:3306/database
mariadb://user:password@host:3306/database
postgresql://user:password@host:5432/database
mongodb://user:password@host:27017/database
```

Using an environment variable is recommended for real deployments:

```bash
export SFTPWARDEN_POSTGRES_DSN='postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden'
```

For production Kubernetes, prefer PostgreSQL, MariaDB/MySQL, or MongoDB over
file-backed providers when users must change outside a deploy cycle. The runtime
reads those providers directly; `sftpwarden refresh` can force a Kubernetes
runtime pod to reload the current database-backed users, and the runtime sync
loop also reconciles periodically. Database providers also avoid carrying
YAML/CSV user entries in generated manifests or Helm values.

Relational SQL providers read and mutate the configured users table. Schema v1
uses these columns:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

User provider schemas:

- `user_schema: 1` stores simple anonymous public keys in `public_keys`.
- `user_schema: 2` stores named keys with metadata. YAML uses a top-level
  `schema_version: 2` and `keys` entries. CSV uses a `keys` JSON column. SQLite,
  MySQL, MariaDB, and PostgreSQL use the configured users table plus
  `sftp_user_keys` for key rows. MongoDB embeds `keys` in each user document.

New `sftpwarden init` projects default to schema v2. Existing configs that omit
`provider.user_schema` continue to behave as schema v1 until explicitly migrated.

YAML schema v2:

```yaml
schema_version: 2
users:
  - username: alice
    keys:
      - name: prod-ci
        public_key: ssh-ed25519 AAAA...
        comment: CI deploy key
        disabled: false
        expires_at: 2027-01-01
```

CSV schema v2 uses a `keys` JSON column:

```text
username,keys,password_hash,uid,gid,upload_dir,comment,disabled
alice,"[{""name"":""prod-ci"",""public_key"":""ssh-ed25519 AAAA...""}]",,,,,,false
```

SQL schema v2 keeps `sftp_users` and adds the key table:

```text
sftp_user_keys:
username, name, public_key, fingerprint, comment, disabled, created_at,
updated_at, expires_at, source, metadata
```

MongoDB schema v2 embeds a `keys` array in each user document and stores
`schema_version: 2` on v2 documents written by SFTPWarden.

When you initialize a project with MySQL, MariaDB, or PostgreSQL, SFTPWarden
checks whether the table exists. If it is missing, interactive `init` asks whether
to create it or abort so you can apply the schema manually. MongoDB performs the
same check for the configured collection and username index.

```bash
sftpwarden init prod \
  --provider mysql \
  --dsn 'mysql://sftpwarden:change-me@db.example.com:3306/sftpwarden' \
  --create-table
```

MariaDB reuses the MySQL-compatible PyMySQL implementation. Installing either
`pip install "sftpwarden[mysql]"` or `pip install "sftpwarden[mariadb]"` enables
both MySQL and MariaDB providers.

Use `--no-create-table` to force init to abort when the table or MongoDB
collection is missing. If you omit `--dsn` in interactive MySQL, MariaDB, or
PostgreSQL init, SFTPWarden asks for host, port, database, username, and
password, then builds the DSN. For MongoDB, interactive init asks for a MongoDB
DSN.

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

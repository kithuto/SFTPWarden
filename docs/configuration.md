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
watcher_mode = "systemd"
sync_interval_seconds = 60
```

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
```

`server.container_port` is not supported. The container SSH port is always `22`;
`server.port` controls the host port exposed by Docker Compose.

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

## Providers

YAML:

```yaml
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
```

CSV:

```yaml
provider:
  type: csv
  path: /etc/sftpwarden/users.csv
```

SQLite:

```yaml
provider:
  type: sqlite
  path: /etc/sftpwarden/users.sqlite
```

SQLite uses Python's built-in `sqlite3` module and does not need an optional
dependency. It is useful for single-host deployments where you want a local
database file instead of YAML or CSV. Avoid SQLite on NFS, high-concurrency, or
multi-writer deployments.

MySQL:

```yaml
provider:
  type: mysql
  dsn: "${SFTPWARDEN_MYSQL_DSN}"
  table: sftp_users
```

MariaDB:

```yaml
provider:
  type: mariadb
  dsn: "${SFTPWARDEN_MARIADB_DSN}"
  table: sftp_users
```

PostgreSQL:

```yaml
provider:
  type: postgresql
  dsn: "${SFTPWARDEN_POSTGRES_DSN}"
  table: sftp_users
```

MongoDB:

```yaml
provider:
  type: mongodb
  dsn: "${SFTPWARDEN_MONGODB_DSN}"
  collection: sftp_users
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

Relational SQL providers read and mutate the configured users table. The default
columns are:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

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

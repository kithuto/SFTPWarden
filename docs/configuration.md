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

MySQL:

```yaml
provider:
  type: mysql
  dsn: "${SFTPWARDEN_MYSQL_DSN}"
  table: sftp_users
```

PostgreSQL:

```yaml
provider:
  type: postgresql
  dsn: "${SFTPWARDEN_POSTGRES_DSN}"
  table: sftp_users
```

SQL providers read and mutate the configured users table. The default columns are:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

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

## Validation

```bash
sftpwarden validate --config sftpwarden.yaml
sftpwarden validate --config sftpwarden.yaml --json
```

CI should validate every example config before release.

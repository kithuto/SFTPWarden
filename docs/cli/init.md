# Init Commands

## `sftpwarden init`

Creates a SFTPWarden project and registers it in the local context registry. It
can create local Compose projects, remote local-sync projects, remote-only
context entries, Kubernetes manifest projects, and Helm projects.

For new projects, `init` writes `sftpwarden.yaml`, creates empty provider
storage, writes deployment files when the target is Compose, and selects the new
context as the active context.

```bash
sftpwarden init dev --yes
sftpwarden init demo --user-schema 1 --yes
sftpwarden init prod --provider postgresql --dsn '${SFTPWARDEN_POSTGRES_DSN}' --create-table
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden --critical
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden --remote-only --critical
sftpwarden init prod --deploy kube --namespace sftpwarden --yes
sftpwarden init prod --deploy helm --namespace sftpwarden --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `context_name` | No | `TEXT` | Context/project name to create. If omitted, interactive mode asks for it. The positional value `remote` is accepted for remote init compatibility. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | none | Context name used by remote init compatibility mode. For normal init, prefer the positional `context_name`. |
| `--provider` | `yaml`, `csv`, `sqlite`, `mysql`, `mariadb`, `postgresql`, `mongodb` | global default, then `yaml` | Selects the user provider type stored in `sftpwarden.yaml`. |
| `--root` | `PATH` | current directory | Local project root to initialize or register. |
| `--remote` | `REMOTE` | none | Creates a remote context from `user@host:/path`. Mutually exclusive with `--remote-url`. |
| `--remote-url` | `REMOTE` | none | Compatibility alias for a remote URL. Mutually exclusive with `--remote`. |
| `--dsn` | `DSN` | none | Database URL for SQL and MongoDB providers. Required with `--yes` for external database providers. |
| `--query` | `TEXT` | provider default | Custom SQL read query for SQL providers. Use only when the table shape is managed outside SFTPWarden. |
| `--table` | `TEXT` | `sftp_users` | SQL users table name for MySQL, MariaDB, PostgreSQL, and SQLite. |
| `--collection` | `TEXT` | `sftp_users` | MongoDB collection name. |
| `--user-schema` | `1` or `2` | `2` | Provider user schema to initialize. v1 is the simple `public_keys` format. v2 is the named-key format and the default for new projects. |
| `--deploy`, `-d` | `compose`, `kube`, `helm` | `compose` | Deployment target stored in `sftpwarden.yaml`. `kube` means Kubernetes manifests. `helm` means Helm mode. |
| `--namespace` | `TEXT` | `sftpwarden` for Kubernetes targets | Kubernetes namespace for `--deploy kube` or `--deploy helm`. Invalid for Compose projects. |
| `--create-namespace` / `--no-create-namespace` | flag pair | prompt, or create with `--yes` | Controls whether a missing Kubernetes namespace is created during init. |
| `--create-table` / `--no-create-table` | flag pair | prompt, or create with `--yes` | Controls whether missing SQL tables or MongoDB storage are created during init. |
| `--host` | `TEXT` | prompt in remote mode | Remote SSH host used when no compact remote URL is supplied. |
| `--user` | `TEXT` | local SSH default | Remote SSH username used when building remote settings. |
| `--port` | `INTEGER` | global default, normally `22` | Remote SSH port. |
| `--remote-root` | `PATH` | global default, normally `~/sftpwarden` | Project root on the remote host. |
| `--ssh-key` | `PATH` | SSH default | Explicit SSH private key path for remote contexts and Docker watcher mode. |
| `--watcher` | `auto`, `systemd`, `openrc`, `runit`, `supervisord`, `launchd`, `windows-task`, `docker` | auto when needed | Watcher backend to install or reuse for remote local-sync contexts. |
| `--remote-only` | flag | false | Registers a context that exists only on the remote host. No local project files are created. |
| `--skip-checks` | flag | false | Skips remote and Kubernetes prerequisite checks. Use when preparing files before the target exists. |
| `--critical` | flag | false | Marks the context as critical so destructive or deployment commands ask for confirmation. |
| `--yes`, `-y` | flag | false | Accepts prompts and defaults. For production-like names without `--critical`, also accepts creating the context as non-critical. |

### Behavior By Target

| Target | What `init` creates |
| --- | --- |
| Local Compose | Local project files, provider storage, `docker-compose.yml`, context registry entry, active context. |
| Remote local-sync | Local editable project, remote context metadata, optional watcher setup, active context. |
| Remote-only | Local registry entry only. SFTPWarden checks the remote host unless `--skip-checks` is used. |
| Kubernetes manifests | Local project plus Kubernetes deployment settings. The namespace is checked or created unless checks are skipped. |
| Helm | Local project plus Helm deployment settings. The namespace is checked or created unless checks are skipped. |

### Database Storage

For SQL and MongoDB providers, `init` checks whether the configured table or
collection exists. If it is missing, interactive mode asks whether to create it.
With `--yes`, SFTPWarden creates it unless `--no-create-table` is provided.

When `--user-schema 2` is selected for SQL providers, storage creation includes
the table needed for named keys.

### When To Use It

Use `init` for new projects. Use `sftpwarden context add` only when a project
already exists and this machine just needs to register it.

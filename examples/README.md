# SFTPWarden Examples

These examples are guides for common SFTPWarden providers and deployment shapes.
They are CLI-first on purpose: deployment artifacts are produced by
`sftpwarden init`, `sftpwarden deploy`, or the dedicated Kubernetes and Helm
commands, not maintained by hand in provider example folders.

| Directory | Shows | Best fit |
| --- | --- | --- |
| `yaml/` | YAML file provider | Quick starts and small GitOps-style deployments |
| `csv/` | CSV file provider | Spreadsheet-friendly user handoff |
| `sqlite/` | SQLite provider | Small single-host deployments |
| `mysql/` | MySQL provider | Existing MySQL-backed platforms |
| `mariadb/` | MariaDB provider | MySQL-compatible MariaDB deployments |
| `postgres/` | PostgreSQL provider | Production database-backed deployments |
| `mongodb/` | MongoDB provider | Existing document databases |
| `kubernetes/` | Kubernetes and Helm examples | Cluster deployments |
| `remote-local-sync/` | Remote context with local source files | Managed remote Docker hosts |
| `remote-only/` | Remote-only context | Existing remote projects |
| `watcher-docker/` | Docker watcher installation | Hosts without a native watcher scheduler |

Start new projects with `sftpwarden init`; it writes the right project config and
generated deployment files for the selected provider:

```bash
mkdir -p ~/sftpwarden-yaml
cd ~/sftpwarden-yaml
sftpwarden init yaml-example --provider yaml --yes
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use checked-out example folders as reference material. YAML and CSV examples can
also be registered and dry-run directly:

```bash
cd examples/yaml
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add yaml-example --root . --yes
sftpwarden deploy --context yaml-example --dry-run
```

Database examples require the matching optional dependency and a real DSN
environment variable. SQLite projects should normally be created with
`sftpwarden init --provider sqlite` so SFTPWarden creates the SQLite provider
database for you.

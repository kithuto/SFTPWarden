# SFTPWarden Examples

Choose one example and follow its steps in a new project directory. These guides
target SFTPWarden 1.3.1 and use the CLI to generate deployment files.

If this is your first SFTP server, start with
[Getting Started](../docs/getting-started.md). It installs the CLI, creates Alice
with a password and then explains the recommended SSH key setup.

## Choose an example

| Directory | Use it for |
| --- | --- |
| [yaml](yaml/README.md) | The simplest file-based setup; YAML is the default. |
| [csv](csv/README.md) | Account lists exchanged through spreadsheets. |
| [sqlite](sqlite/README.md) | A small single-host database without an external server. |
| [mysql](mysql/README.md) | An existing MySQL database. |
| [mariadb](mariadb/README.md) | An existing MariaDB database. |
| [postgres](postgres/README.md) | An existing PostgreSQL database. |
| [mongodb](mongodb/README.md) | Accounts stored as MongoDB documents. |
| [kubernetes](kubernetes/README.md) | Kubernetes manifests or Helm. |
| [remote-local-sync](remote-local-sync/README.md) | Local project files and a remote Docker server. |
| [remote-only](remote-only/README.md) | A project maintained on the remote server. |
| [watcher-docker](watcher-docker/README.md) | Provider delivery through a Docker watcher. |

## Start a project

YAML needs no optional database package:

```bash
mkdir -p ~/sftpwarden-yaml
cd ~/sftpwarden-yaml
sftpwarden init yaml-example --yes
sftpwarden config server.host 127.0.0.1
sftpwarden user create alice --no-refresh
sftpwarden deploy
```

Init uses schema v2 with named keys. Use `--provider TYPE` to override the
provider for one project or `sftpwarden config default-provider TYPE` to change
the default for future projects.

## Use checked-out files as references

The YAML and CSV files illustrate storage formats. Their sample credentials
must be replaced before use; the public-only example keys have no available
private keys. Validate a configuration from the repository root with:

```bash
sftpwarden validate --config examples/yaml/sftpwarden.yaml
```

Validation checks configuration and paths. It does not prove database
connectivity, valid credentials or a running SFTP service. Database examples need
real infrastructure and the matching extra. Use `init --provider sqlite` to
create a real SQLite database before attempting provider operations.

For verification, each deployment should pass health and an authenticated file
transfer. See [Providers](../docs/providers.md) for formats and
[Operations](../docs/operations.md) for activation rules.

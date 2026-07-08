# SFTPWarden Documentation

SFTPWarden is a CLI-first, container-native SFTP management tool. It keeps the
OpenSSH runtime small, stores operational state outside the image, and lets
operators manage local, remote, and Kubernetes deployments through explicit
commands and declarative providers.

## Start Here

- [Getting Started](getting-started.md): install the CLI, create a first project,
  add a user, deploy, and choose the right next guide.
- [Operations](operations.md): understand deploy, refresh, watcher sync,
  backup/restore, health checks, remote cleanup, Kubernetes, and Helm workflows.
- [Configuration](configuration.md): configure `sftpwarden.yaml`, contexts,
  deployment targets, runtime settings, and provider storage.

## Core Topics

- [Providers](providers.md): choose YAML, CSV, SQLite, MySQL, MariaDB,
  PostgreSQL, or MongoDB, and understand schema v1 versus schema v2.
- [Named Keys](named-keys.md): manage schema v2 SSH keys with names, metadata,
  rotation, expiry, disable/enable, import, and migration from schema v1.
- [Security](security.md): review chroot, passwords, host keys, secrets,
  backups, remote access, watcher behavior, and runtime boundaries.

## Reference

- [CLI Reference](cli-reference.md): command-by-command behavior and examples
  from the real Typer command surface.
- [Contributing](contributing.md): development setup, validation, release checks,
  documentation build, and GitHub Pages publication.

```{toctree}
:hidden:
:maxdepth: 2
:caption: Start Here

self
getting-started
operations
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Configure

configuration
providers
named-keys
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Reference

cli-reference
cli/init
cli/core
cli/users
cli/user-keys
cli/providers
cli/contexts
cli/config
cli/watcher
cli/kubernetes
cli/helm
cli/runtime
security
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Project

contributing
```

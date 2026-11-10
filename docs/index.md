# SFTPWarden Documentation

SFTPWarden helps you run an SFTP server and manage who can upload or download
files. It runs OpenSSH in a container and gives you a command-line interface to
manage accounts, access keys and deployments.

**Automatic user synchronization is a core feature.** Add an account with a CLI
command or edit the configured YAML file, database or other provider directly.
SFTPWarden keeps the running server's accounts aligned with that source, so you
can add access whenever it is needed. Read
[how synchronization works](operations.md#automatic-user-synchronization) for
timing and how changes reach each deployment type.

Start with one server on your computer. The same CLI can manage a Docker server
over SSH or deploy to Kubernetes with manifests or Helm. This documentation
covers SFTPWarden **1.3.1**.

## Start Here

| What you want to do | Read |
| --- | --- |
| Try SFTPWarden and transfer your first file | [Getting Started](getting-started.md) |
| Change the port or understand project settings | [Configuration](configuration.md) |
| Manage users or operate a remote server | [Operations](operations.md) |
| Recover an installation or upgrade an existing one | [Recovery](recovery.md) |

If you are new to DevOps, follow Getting Started in order. It explains each
component as you use it. If you already operate SFTP servers, start with the
deployment choices in Operations and use the reference for exact options.

## Core Topics

Four terms connect the guides:

| Term | Meaning |
| --- | --- |
| Project | Your configuration and deployment files, usually in one directory. |
| Context | A saved name for the installation a command should manage. |
| Provider | The file or database that stores SFTP accounts and their credentials. |
| Runtime | The OpenSSH container that accepts connections and applies user access. |

The CLI edits the provider, which you can also update manually or through another
system. The runtime reads it automatically and applies the resulting access
rules. Uploaded files live in separate persistent storage.

- [Providers](providers.md): choose storage for accounts and understand its format.
- [Named Keys](named-keys.md): add, rotate or revoke a specific SSH key.
- [Security](security.md): understand isolation and prepare access policies.
- [Configuration Effects](configuration-effects.md): check how to activate a change.

**Deploy applies configuration. Refresh applies users already available to the
runtime.** For remote local-sync projects, the watcher delivers provider file
changes. Kubernetes file providers need a deployment to deliver changed files.

## Reference

Use [CLI Reference](cli-reference.md) when you know the task and need command
options. Use [Contributing](contributing.md) to work from source, build the docs
or validate a change.

```{toctree}
:hidden:
:maxdepth: 2
:caption: Start Here

self
getting-started
operations
recovery
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Configure

configuration
configuration-effects
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
cli/upgrade
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

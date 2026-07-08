# Getting Started

This guide gets a local SFTPWarden project running and points you to the next
documentation page for each common path.

## Install

Install the CLI:

```bash
python -m pip install sftpwarden
```

For source checkouts and documentation work:

```bash
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
```

Docker is required for the default Compose runtime. Kubernetes projects also
need `kubectl`; Helm projects need `helm`.

## Create a First Project

The quickest start uses YAML and user schema v1, which stores anonymous
`public_keys` directly on each user:

```bash
sftpwarden config default-provider yaml
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --user-schema 1 --yes
```

Schema v1 is fully supported and is the simplest format for small projects. New
projects default to schema v2 when `--user-schema` is omitted; schema v2 adds
named keys, per-key metadata, expiry, disable/enable, rotation, and imports.

To start directly with schema v2:

```bash
sftpwarden init dev --user-schema 2 --yes
```

## Add a User

Password user:

```bash
sftpwarden user create alice --password "correct horse battery staple"
```

Public key user:

```bash
sftpwarden user create alice --public-key ./alice.pub
```

Schema v2 named key:

```bash
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub
```

## Deploy and Refresh

Start or update the runtime:

```bash
sftpwarden deploy
```

Apply user/provider changes to an already running runtime:

```bash
sftpwarden refresh
```

Use `deploy` after changing config, Compose, Kubernetes, Helm, or remote
deployment settings. Use `refresh` for user changes already visible to the
runtime.

## Check the Project

Validate configuration and provider access:

```bash
sftpwarden validate
```

Inspect runtime and project health:

```bash
sftpwarden health
```

Preview runtime user changes without applying them:

```bash
sftpwarden plan
```

## Choose the Next Guide

| Need | Read |
| --- | --- |
| Understand local, remote, Kubernetes, and Helm operations | [Operations](operations.md) |
| Edit `sftpwarden.yaml` and contexts safely | [Configuration](configuration.md) |
| Pick YAML, CSV, SQLite, SQL, or MongoDB | [Providers](providers.md) |
| Manage named SSH keys and migrations | [Named Keys](named-keys.md) |
| Automate or script exact commands | [CLI Reference](cli-reference.md) |
| Harden a deployment before exposure | [Security](security.md) |

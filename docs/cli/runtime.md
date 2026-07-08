# Runtime Commands

Runtime commands are intended to run inside the SFTPWarden container. Most
operators do not need to run them directly from a workstation.

The generated Compose and Kubernetes deployments use these commands to plan,
apply, loop, and health-check runtime user state.

## `sftpwarden runtime`

Command group for container-side runtime operations.

```bash
sftpwarden runtime --help
```

Subcommands:

- `plan`
- `refresh`
- `sync`
- `health`

## `sftpwarden runtime plan`

Calculates the runtime synchronization plan without applying it.

```bash
sftpwarden runtime plan --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime plan --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `/etc/sftpwarden/sftpwarden.yaml` | Runtime config path inside the container. |
| `--json` | flag | false | Prints the runtime plan as JSON. |

### Effects

Read-only. It loads config, provider users, and runtime state, then prints what a
refresh would do.

## `sftpwarden runtime refresh`

Runs one forced runtime synchronization pass.

```bash
sftpwarden runtime refresh --config /etc/sftpwarden/sftpwarden.yaml
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `/etc/sftpwarden/sftpwarden.yaml` | Runtime config path inside the container. |

### Effects

Applies provider users to Linux users, UID/GID state, home/upload directories,
and OpenSSH authorized keys.

## `sftpwarden runtime sync`

Runs the long-lived runtime synchronization loop.

```bash
sftpwarden runtime sync --config /etc/sftpwarden/sftpwarden.yaml
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `/etc/sftpwarden/sftpwarden.yaml` | Runtime config path inside the container. |

### Effects

Keeps running and reconciles provider state according to runtime sync settings.

## `sftpwarden runtime health`

Checks runtime-internal health without applying changes.

```bash
sftpwarden runtime health --config /etc/sftpwarden/sftpwarden.yaml
sftpwarden runtime health --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--config` | `PATH` | `/etc/sftpwarden/sftpwarden.yaml` | Runtime config path inside the container. |
| `--json` | flag | false | Prints runtime health checks as JSON and exits `0` when healthy or `1` when unhealthy. |

### Effects

Read-only. The generated Docker Compose healthcheck uses this command. Kubernetes
probes are configured from the `kubernetes.*_probe.*` settings in
`sftpwarden.yaml`.

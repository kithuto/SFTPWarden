# Runtime Commands

Runtime commands are intended to run inside the SFTPWarden container. Most
operators do not need to run them directly from a workstation.

Use the workstation commands `sftpwarden refresh` and `sftpwarden health` for
normal operation. For a local Compose diagnostic, run:

```bash
docker compose exec sftpwarden sftpwarden runtime health --json
```

All direct `runtime` examples below run **inside the container**. The generated
deployment uses them to apply user policy and check process or synchronization
health. They do not apply new deployment configuration.

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

Read-only. It loads config, provider users and runtime state, then prints what a
refresh would do.
It uses the applied configuration captured by container startup. A missing or
invalid applied snapshot is an error; recreate the runtime with deploy.

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

Applies provider users to Linux users, UID/GID state, home/upload directories
and OpenSSH authorized keys.
It does not render or reload `sshd_config`. An undeployed provider configuration
change blocks manual refresh; other pending config changes are reported and the
deployed settings remain active.
For local and remote `local-sync` Compose contexts, the host CLI compares desired
configuration fingerprints with the deployed snapshot. This also detects atomic
host-file replacement when a single-file container mount still sees the old
config. Direct container commands can inspect only the configuration available
inside that container.

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
Successful passes record each user's effective policy. Later periodic passes
update only affected users, including credentials that expire without a provider
edit. Metadata-only changes do not reapply accounts or permissions. An interrupted
application is repaired before synchronization can report success, even if the
provider was reverted. Use `runtime refresh` for a forced repair of all users.

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
| `--probe` | `readiness`, `liveness`, `startup` | `readiness` | Selects policy readiness or process liveness. |

### Effects

Read-only. The generated Docker Compose healthcheck uses this command. Kubernetes
probes are configured from the `kubernetes.*_probe.*` settings in
`sftpwarden.yaml`.

The container supervisor owns OpenSSH and, when enabled, the synchronization
worker. Initial policy must succeed before OpenSSH starts. If either required
child exits, the supervisor shuts down the other; the configured deployment
restart policy controls restart. Provider errors are retried with delays of
1, 2, 4, 8, 16 and then at most 30 seconds. The last applied authorization is
retained, including installed OpenSSH expiry deadlines.

Readiness checks current process identities, a supervisor heartbeat no older
than 5 seconds, the local OpenSSH listener and successful reconciliation.
An unapplied failure immediately makes readiness false. For enabled sync, the
maximum success age is `sync.interval_seconds + 30 + 30 + 5` seconds (one
interval, attempt budget, retry cap and scheduling allowance). A pending attempt
may not exceed 30 seconds. Liveness checks processes and the listener without
treating a provider outage as a reason to restart. Disabled periodic sync still
requires successful initial policy, but has no recurring-success age limit.

Reconciliation runs in a separate process group with a 30-second deadline,
including its maximum 5-second runtime lock wait. Initial startup permits
60 seconds. Shutdown gives each of the two children at most 5 seconds before
forcing termination. Probes never connect to a provider or wait for this lock;
their local TCP check allows one second. External project health allows ten
seconds for its selected Compose, SSH or Kubernetes command and fails when the
runtime is unreachable or non-ready.

Private `lifecycle.json` and `sync-status.json` records live beside runtime state.
They contain process generations, UTC attempt/success times, monotonic deadlines
and sanitized failure categories. A stale record or reused PID cannot establish
health. These files are operational status, not an audit log. A failed refresh
returns nonzero; inspect status and repair the provider before retrying.

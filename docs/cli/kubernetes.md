# Kubernetes Commands

`sftpwarden kube` is for projects configured for Kubernetes manifest mode. These
commands use values from `sftpwarden.yaml`, especially `deploy.target`,
`kubernetes.mode`, `kubernetes.namespace`, `kubernetes.release`, and
`kubernetes.kube_context`.

## `sftpwarden kube`

Command group for Kubernetes manifest operations.

```bash
sftpwarden kube --help
```

Subcommands:

- `render`
- `apply`
- `status`
- `logs`
- `doctor`
- `delete`

## `sftpwarden kube render`

Renders Kubernetes manifests and prints them to stdout. It does not contact a
cluster.

```bash
sftpwarden kube render
sftpwarden kube render --context prod
sftpwarden kube render --config ./sftpwarden.yaml
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Renders from a config path directly. |

### Effects

Read-only. It prints rendered manifest YAML.

## `sftpwarden kube apply`

Applies rendered Kubernetes manifests with `kubectl` and restarts the runtime
StatefulSet so deployment changes are remounted or reloaded.

```bash
sftpwarden kube apply
sftpwarden kube apply --context prod
sftpwarden kube apply --dry-run
sftpwarden kube apply --dry-run --json
sftpwarden kube apply --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--dry-run` | flag | false | Prints the Kubernetes deployment plan and pending provider schema reconciliation without applying anything. |
| `--json` | flag | false | Prints the dry-run plan as JSON. In non-dry-run mode, prints the command result as JSON after success. |
| `--yes`, `-y` | flag | false | Accepts pending forward provider schema migration confirmation. |

### Effects

A real apply can:

- migrate provider data forward when `provider.user_schema` requires it;
- write `kubernetes.yml` in the project root;
- run `kubectl apply -f kubernetes.yml` with configured namespace/context;
- run `kubectl rollout restart statefulset/<release>`;
- sync YAML/CSV provider data into the provider PVC as part of the deployment plan.

## `sftpwarden kube status`

Checks Kubernetes resources for the configured release.

```bash
sftpwarden kube status
sftpwarden kube status --context prod
sftpwarden kube status --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--json` | flag | false | Prints namespace, release, and check results as JSON. |

### Checks

The command runs `kubectl get` checks for:

- namespace;
- runtime StatefulSet;
- pods matching the release selector;
- service;
- PVCs matching the release selector.

## `sftpwarden kube logs`

Shows logs from the runtime StatefulSet's `sftpwarden` container.

```bash
sftpwarden kube logs
sftpwarden kube logs --context prod
sftpwarden kube logs --follow
sftpwarden kube logs -f
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--follow`, `-f` | flag | false | Streams logs by passing `--follow` to `kubectl logs`. |

### Command Run

The target is `statefulset/<kubernetes.release>` and the container is
`sftpwarden`.

## `sftpwarden kube doctor`

Validates Kubernetes access and key resources.

```bash
sftpwarden kube doctor
sftpwarden kube doctor --context prod
sftpwarden kube doctor --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--json` | flag | false | Prints check results as JSON. |

### Checks

The command checks:

- `kubectl version --client`;
- configured namespace;
- host-key secret for the release;
- configured storage class, when `kubernetes.storage_class` is set.

Provider and probe configuration are validated during manifest rendering.

## `sftpwarden kube delete`

Deletes rendered Kubernetes resources with explicit confirmation.

```bash
sftpwarden kube delete
sftpwarden kube delete --dry-run
sftpwarden kube delete --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--yes`, `-y` | flag | false | Accepts the deletion confirmation prompt. |
| `--dry-run` | flag | false | Prints the `kubectl delete` command without running it. |

### Effects

A real delete writes the current `kubernetes.yml`, then runs
`kubectl delete -f kubernetes.yml --ignore-not-found` with the configured
namespace/context.

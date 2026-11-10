# Helm Commands

`sftpwarden helm` is for projects configured with Kubernetes Helm mode. The
commands use `helm`, values rendered from `sftpwarden.yaml` and the configured
Kubernetes namespace/release.

Source checkouts use the local `charts/sftpwarden` chart. Installed packages use
the published OCI chart reference pinned to the installed SFTPWarden version.

For a first deployment, follow [Operations](../operations.md#helm). The examples
below are alternatives. A real upgrade can interrupt transfers.

CLI upgrade prepares host identity and explicitly replaces the Pod. Direct Helm
uses an `OnDelete` StatefulSet and requires the ordered
[preparation and activation procedure](../recovery.md#kubernetes-identity-and-storage).

## `sftpwarden helm`

Command group for Helm chart operations.

```bash
sftpwarden helm --help
```

Subcommands:

- `values`
- `template`
- `lint`
- `upgrade`
- `uninstall`

## `sftpwarden helm values`

Renders Helm values from `sftpwarden.yaml`.

```bash
sftpwarden helm values
sftpwarden helm values --context prod
sftpwarden helm values --config ./sftpwarden.yaml
sftpwarden helm values --write
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Renders from a config path directly. |
| `--write` | flag | false | Writes `values.yaml` to the project root. Without it, values text is printed. |

### Effects

Without `--write`, read-only. With `--write`, updates `values.yaml` but does not
install or upgrade the release.

## `sftpwarden helm template`

Runs `helm template` for the configured release and chart.

```bash
sftpwarden helm template
sftpwarden helm template --context prod
sftpwarden helm template --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--json` | flag | false | Prints the Helm command and rendered output as JSON. |

### Effects

Writes current `values.yaml` in the project root, runs `helm template` and
prints the rendered manifests. It does not apply anything to a cluster.

## `sftpwarden helm lint`

Runs `helm lint` for the configured chart.

```bash
sftpwarden helm lint
sftpwarden helm lint --context prod
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |

### Effects

If a local project root exists, SFTPWarden writes current `values.yaml` before
linting. For published charts, it pulls the chart to a temporary directory before
running lint.

## `sftpwarden helm upgrade`

Shares deploy's version inspection, backup recommendation, migration confirmation
and activated-version verification. Required image/chart preflight precedes an
upgrade's migrations. See [deploy](core.md#sftpwarden-deploy) and [Upgrade](upgrade.md).

Installs or upgrades the Helm release and restarts the runtime StatefulSet after
Helm succeeds.

```bash
sftpwarden helm upgrade --install
sftpwarden helm upgrade --context prod --install
sftpwarden helm upgrade --dry-run
sftpwarden helm upgrade --dry-run --json
sftpwarden helm upgrade --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--install` | flag | false | Keeps Helm's `--install` behavior. Without this flag, the generated upgrade command removes `--install`. |
| `--dry-run` | flag | false | Prints the Helm deployment plan and pending provider schema reconciliation without applying anything. |
| `--json` | flag | false | Prints dry-run plan and commands as JSON. In non-dry-run mode, prints result JSON after success. |
| `--yes`, `-y` | flag | false | Accepts pending forward provider schema migration confirmation. |

### Effects

A real upgrade can:

- migrate provider data forward when `provider.user_schema` requires it;
- write `values.yaml`;
- run `helm upgrade` or `helm upgrade --install` with configured chart, namespace and values;
- prepare and verify durable host identity before updating resources;
- explicitly replace the Pod and verify applied configuration and fingerprints;
- sync YAML/CSV provider data into the provider PVC as part of the deployment plan.

## `sftpwarden helm uninstall`

Uninstalls the configured Helm release with explicit confirmation.

```bash
sftpwarden helm uninstall
sftpwarden helm uninstall --dry-run
sftpwarden helm uninstall --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--yes`, `-y` | flag | false | Accepts the uninstall confirmation prompt. |
| `--dry-run` | flag | false | Prints the `helm uninstall` command without running it. |

### Effects

A real uninstall runs `helm uninstall <release> --namespace <namespace>` with
the configured kube context when one is set.

Before uninstall, the CLI verifies that the installed Helm release records the
retention policy for PVCs and Secrets. A live annotation alone is insufficient.
Complete the prepared upgrade first for a legacy release. Retained claims can be
reused under the same release name and namespace; do not delete them to resolve
an ownership error. See [Recovery and upgrades](../recovery.md).

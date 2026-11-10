# SFTPWarden Helm Chart

This chart runs one SFTPWarden OpenSSH container as a StatefulSet. It persists
uploaded files, runtime account state and host keys. File providers also have
persistent storage.

For a first deployment, use the
[CLI Helm workflow](../../docs/operations.md#helm). This guide covers direct
chart use from a source checkout for version 1.4.0.

## Prerequisites

You need Helm, a configured Kubernetes cluster and a StorageClass that can bind
the required PVCs. The nodes must be able to pull the runtime image.

The default namespace and resource release name are both `sftpwarden`.
Keep the Helm release namespace and rendered resource namespace consistent.
The default Service is ClusterIP; plan cluster networking for external access.

## Prepare values and install

The default YAML provider contains no accounts. Prepare your provider and
runtime configuration before expecting SFTP login to work. The CLI can generate
consistent values from an initialized Helm project. Select that project's
context before running:

```bash
sftpwarden helm values --write
```

For direct chart validation from the repository root:

```bash
helm lint charts/sftpwarden -f /path/to/project/values.yaml
helm template sftpwarden charts/sftpwarden \
  --namespace sftpwarden -f /path/to/project/values.yaml
```

Replace the values path with your generated file. Review the output, then install
a new release:

```bash
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden --create-namespace \
  -f /path/to/project/values.yaml
helm test sftpwarden --namespace sftpwarden
```

Check Pod readiness and complete an authenticated SFTP upload/download.
The test hook alone does not prove that your user credentials work.

## Provider choices

YAML/CSV `provider.bootstrapContent` is copied into the provider PVC on each
rollout. Review it carefully: the deployed file becomes the runtime's account
source. An empty string leaves existing provider data untouched; an explicit
`users: []` is an empty user set. Generated values may contain password hashes
and public-key metadata, so restrict access.

Use an external database when accounts need to change independently of
deployment. For PostgreSQL, MySQL/MariaDB or MongoDB, prepare the database and
provision an operator-managed Secret containing the DSN. Set
`provider.dsnSecretName` and reference its variable in `sftpwardenConfig`.
See the [PostgreSQL example](../../examples/kubernetes/README.md#postgresql-values-example).

The chart can create a DSN Secret with `provider.createDsnSecret=true`, but that
places credentials in the values supplied to Helm. Prefer separately managed
Secrets. SQLite needs a preinitialized provider PVC and is intended for
single-Pod labs.

## Common settings

| Setting | Purpose |
| --- | --- |
| `persistence.data.size` | SFTP file capacity; defaults to `10Gi`. |
| `kubernetes.storageClass` | StorageClass for claims. |
| `service.type` / `service.port` | How the SFTP Service is exposed. |
| `probes.startup` | Time allowed for initial runtime preparation. |
| `probes.readiness` | Whether the runtime can serve the expected policy. |
| `probes.liveness` | Whether required runtime processes remain available. |
| `hostKeys.secretName` | Optional existing host-key Secret; otherwise keys use a retained PVC. |
| `runtime.replicas` | Must remain `1` in 1.4.0. |

Set these in your values file. For example, `persistence.data.size: 50Gi`
requests more storage. The StorageClass must support expansion; Kubernetes does
not shrink existing PVCs.

## Upgrade and activate

For an existing installation, first follow the ordered
[identity preparation procedure](../../docs/recovery.md#kubernetes-identity-and-storage).
Preserve a verified recovery point.

The StatefulSet uses `OnDelete`. A direct `helm upgrade` updates resources but
does not replace the current Pod. After preparation and upgrade, explicitly
delete the selected `<release>-0` Pod to activate the new configuration. Verify
readiness, the original host-key fingerprints and authenticated transfers.

Do not rely on `kubectl rollout restart` to activate an `OnDelete` StatefulSet.
The SFTPWarden CLI handles preparation and explicit activation when you use
`sftpwarden deploy` or `sftpwarden helm upgrade`.

## Storage retention

Data, state, provider and host-key PVCs are retained. Operator-managed Secrets
keep their ownership. Before uninstalling a legacy release, ensure the Helm
release record includes retention; a live annotation alone is insufficient.
The CLI refuses uninstall when that preparation is missing.

Retained storage can be reused under the same namespace and release name.
Deleting claims is a separate destructive action and still depends on the PV
reclaim policy. Do not delete storage or rotate host keys to work around an
upgrade failure.

# Kubernetes Example

This example shows SFTPWarden Kubernetes manifest and Helm workflows. Use it when
you want cluster-managed storage, probes, namespace handling, and a
database-backed provider for production-style deployments.

Create a Kubernetes manifest project:

```bash
sftpwarden init prod --deploy kube --yes
sftpwarden kube render
sftpwarden deploy --dry-run
sftpwarden kube apply
```

Create a Helm project:

```bash
sftpwarden init prod --deploy helm --yes
sftpwarden helm values --write
sftpwarden helm template
sftpwarden deploy --dry-run
sftpwarden helm upgrade --install
```

`init` checks the Kubernetes namespace for both manifest and Helm projects. If
the namespace does not exist, interactive init asks whether to create it; `--yes`
creates the default `sftpwarden` namespace automatically. Use
`--namespace <name>` for another namespace, or `--no-create-namespace` when
cluster policy requires the namespace to be created beforehand.

The SFTP upload PVC defaults to `10Gi`. For generated SFTPWarden projects,
increase it before deploying with:

```bash
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden deploy --dry-run
```

Probe timings are configurable in generated projects before deploy:

```bash
sftpwarden config kubernetes.startup_probe.failure_threshold 60
sftpwarden config kubernetes.liveness_probe.period_seconds 45
sftpwarden deploy --dry-run
```

YAML and CSV providers are declarative in Kubernetes examples. After editing
`users.yaml` or `users.csv`, run `sftpwarden deploy`, `sftpwarden kube apply`, or
`sftpwarden helm upgrade --install`; the rollout copies the rendered provider
file into the provider PVC. `sftpwarden refresh` reloads users already visible in
the pod, but it does not copy local YAML/CSV files into the cluster. Keep those
provider files and generated manifests or values in the same review process,
because the rendered deployment contains the user entries that will be copied.

The PostgreSQL values example expects a Secret named `sftpwarden-provider`:

```bash
kubectl create namespace sftpwarden
kubectl create secret generic sftpwarden-provider \
  --namespace sftpwarden \
  --from-literal=SFTPWARDEN_PROVIDER_DSN='postgresql://user:password@db:5432/sftpwarden'
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden \
  -f examples/kubernetes/values-postgresql.yaml
```

Use PostgreSQL, MariaDB/MySQL, or MongoDB providers for production Kubernetes
deployments, especially when user data must update outside deploy cycles. The
runtime reads those providers directly and can reconcile them through its sync
loop or an explicit refresh. YAML/CSV are best for GitOps-style deployments, and
SQLite is only for single-pod lab use.

SFTPWarden v1.2 supports `replicas: 1` only. Higher replica counts are reserved
for future multi-node work and are rejected by the CLI and Helm chart schema.

# Kubernetes Example

Create a Kubernetes-targeted project:

```bash
sftpwarden init prod --deploy kube --yes
sftpwarden kube render
sftpwarden deploy --dry-run
sftpwarden kube apply
```

For Helm:

```bash
sftpwarden init prod --deploy helm --yes
sftpwarden helm values --write
sftpwarden helm template
sftpwarden deploy --dry-run
sftpwarden helm upgrade --install
```

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
deployments. YAML/CSV are best for GitOps-style deployments, and SQLite is only
for single-pod lab use.

SFTPWarden v1.2 supports `replicas: 1` only. Higher replica counts are reserved
for future multi-node work and are rejected by the CLI and Helm chart schema.

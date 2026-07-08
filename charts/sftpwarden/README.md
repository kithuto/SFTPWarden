# SFTPWarden Helm Chart

This chart deploys one SFTPWarden OpenSSH runtime as a Kubernetes StatefulSet.

`runtime.replicas` is intentionally limited to `1` in v1.3. Values greater than
`1` are reserved for future multi-node work and are rejected by the chart schema.

By default, resources are installed into the `sftpwarden` namespace. Install the
release with `--namespace sftpwarden --create-namespace` so the Helm release
namespace and rendered resource namespace match.

The chart persists SFTP data, runtime state, host keys, and file-backed provider
data. The default values use provider schema v2 with an empty YAML provider:
`schema_version: 2` and `users: []`. For YAML/CSV providers, the init container
copies `provider.bootstrapContent` into the provider PVC on each rollout. Leave
`provider.bootstrapContent` empty when existing provider data should be left
untouched by Helm upgrades.

The SFTP user data PVC defaults to `10Gi`. Increase it with
`persistence.data.size`:

```bash
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden \
  --set persistence.data.size=50Gi
kubectl rollout restart statefulset/sftpwarden --namespace sftpwarden
```

Your StorageClass must allow volume expansion, and Kubernetes does not shrink
existing PVCs.

Runtime probes run `sftpwarden runtime health` inside the container. Tune them
with `probes.startup`, `probes.readiness`, and `probes.liveness`:

```bash
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden \
  --set probes.startup.failureThreshold=60 \
  --set probes.liveness.periodSeconds=45
```

Use PostgreSQL, MariaDB/MySQL, or MongoDB providers for serious Kubernetes
deployments. YAML/CSV fit GitOps-style workflows, and SQLite is only appropriate
for single-pod lab deployments.

For database providers, create the DSN Secret before installing the release:

```bash
kubectl create namespace sftpwarden
kubectl create secret generic sftpwarden-provider \
  --namespace sftpwarden \
  --from-literal=SFTPWARDEN_PROVIDER_DSN='postgresql://user:password@db:5432/sftpwarden'
```

Set `provider.dsnSecretName` and reference the same environment variable in
`sftpwardenConfig`. The chart can create the Secret when
`provider.createDsnSecret=true`, but storing real DSNs in values files is not
recommended.

Validate the chart and rendered manifests locally:

```bash
helm lint charts/sftpwarden
helm template sftpwarden charts/sftpwarden --namespace sftpwarden
```

Install into the default namespace used by the chart:

```bash
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden \
  --create-namespace
```

After installing, run the chart test hook:

```bash
helm test sftpwarden --namespace sftpwarden
```

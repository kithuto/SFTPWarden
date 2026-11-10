# Kubernetes Example

Deploy one SFTP runtime with persistent storage using Kubernetes manifests or
Helm. Choose one workflow; they are alternatives.

You need a cluster with a usable StorageClass, `kubectl` configured for that
cluster and permission to create the required resources. Helm mode also needs
Helm. Cluster nodes must be able to pull the runtime image.

## Choose a workflow

### Kubernetes manifests

```bash
mkdir -p ~/sftpwarden-cluster
cd ~/sftpwarden-cluster
sftpwarden init cluster --deploy kube --yes
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden kube apply
```

### Helm

```bash
mkdir -p ~/sftpwarden-helm
cd ~/sftpwarden-helm
sftpwarden init helm-demo --deploy helm --yes
sftpwarden user create alice --no-refresh
sftpwarden helm lint
sftpwarden deploy --dry-run
sftpwarden helm upgrade --install
```

Both use YAML by default. Enter Alice's password when prompted.
Init checks the namespace; `--yes` accepts creating the default `sftpwarden`
namespace if it is missing. Use `--namespace NAME` for another namespace or
`--no-create-namespace` when it must already exist.

## Verify the server

```bash
sftpwarden kube status
sftpwarden health
kubectl get service --namespace sftpwarden
```

For the default ClusterIP Service, forward a local port in a separate terminal:

```bash
kubectl port-forward --namespace sftpwarden service/cluster 2222:2222
```

For the Helm example, use `service/helm-demo` instead. Then connect with
`sftp -P 2222 alice@127.0.0.1`. Compare the presented server
fingerprint with the matching host key in the trusted Pod before accepting it.
Upload and download a file, then follow
[Alice's SSH key setup](../../docs/getting-started.md#use-an-ssh-key-recommended).
For YAML here, deploy after adding the key to deliver the local provider change.

The default Service is private to the cluster. Permanent external access
requires your cluster's Service/network configuration.

## Provider updates and storage

YAML/CSV files are copied into the provider PVC on deployment. Editing a local
file requires `deploy`, `kube apply` or `helm upgrade --install`.
Refresh only reloads users already visible in the Pod. Rendered manifests and
values contain those user entries; protect them like the provider.

Database providers let account changes reach the runtime independently of
deployment. SQLite requires an explicitly initialized provider PVC and is
appropriate only for single-Pod labs; local SQLite files are not bootstrapped.

SFTP user storage defaults to `10Gi`. To request more:

```bash
sftpwarden config kubernetes.data_storage_size 50Gi
sftpwarden deploy --dry-run
sftpwarden deploy
```

Your StorageClass must support expansion. Shrinking existing PVCs is unsupported.
The runtime supports one replica in 1.3.1.

## PostgreSQL values example

`values-postgresql.yaml` is a direct Helm example for an existing schema-v2
database. Prepare its user and key tables and provision a Secret named
`sftpwarden-provider` with the `SFTPWARDEN_PROVIDER_DSN` key through your secret
management workflow. Database connectivity must work from the Pod.

From the repository root, review the example's namespace and StorageClass before
a **new** installation:

```bash
helm lint charts/sftpwarden -f examples/kubernetes/values-postgresql.yaml
helm upgrade --install sftpwarden charts/sftpwarden \
  --namespace sftpwarden \
  -f examples/kubernetes/values-postgresql.yaml
```

The namespace must exist because it contains the prepared Secret. Do not put
real DSNs into values files or command history.

## Existing installations

Direct Helm and manifest upgrades need identity preparation and explicit Pod
activation. Follow [Recovery](../../docs/recovery.md#kubernetes-identity-and-storage)
before changing an existing release. CLI deploy performs these steps for its
registered project. Host keys use retained storage by default; preserve it with
the data and runtime state.

See the [chart guide](../../charts/sftpwarden/README.md) for direct Helm settings.

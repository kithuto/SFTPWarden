# YAML Example

This example shows the YAML file provider. It is the smallest SFTPWarden setup
and is a good fit for quick starts, demos, and small GitOps-style deployments.

Create a new YAML-backed project:

```bash
mkdir -p ~/sftpwarden-yaml
cd ~/sftpwarden-yaml
sftpwarden init yaml-example --provider yaml --yes
sftpwarden user add alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example as a reference or local smoke test:

```bash
cd examples/yaml
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add yaml-example --root . --yes
sftpwarden deploy --context yaml-example --dry-run
```

The provider file is `users.yaml`. Replace the example password hash before
using it outside local testing.

Use `sftpwarden refresh --context yaml-example` after changing users and
`sftpwarden deploy --context yaml-example` after changing `sftpwarden.yaml`.

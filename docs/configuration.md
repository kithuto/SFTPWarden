# Configuration

SFTPWarden uses two CLI-level files and one project-level file.

## Global CLI Config

Path: `~/.sftpwarden/config.toml`

```toml
version = 1
default_provider = "yaml"

[defaults]
root = "~/sftpwarden"
remote_root = "~/sftpwarden"
ssh_port = 22
remote_storage = "local-sync"
watcher_mode = "systemd"
sync_interval_seconds = 60
```

Provider selection order:

1. `--provider`
2. `SFTPWARDEN_DEFAULT_PROVIDER`
3. `~/.sftpwarden/config.toml`
4. built-in fallback `yaml`

## Context Registry

Path: `~/.sftpwarden/contexts.toml`

The registry stores local, remote local-sync, and remote-only contexts. It does not store provider secrets.

When a remote context has `ssh_key = "default"`, SSH uses the default identity lookup for that user instead of an explicit `-i` key path.

## Project Config

Path: `<project-root>/sftpwarden.yaml`

Minimum valid config:

```yaml
version: 1
project:
  name: sftpwarden
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
```

`project.name` is required. `server.container_port` is invalid because the container SSH port is always `22`; use `server.port` for the host port.

Password authentication is enabled by default:

```yaml
auth:
  allow_public_key: true
  allow_password: true
  recommended: password
```

For key-only deployments, add public keys to users and disable password login:

```yaml
auth:
  allow_public_key: true
  allow_password: false
  recommended: public_key
```

Watcher config is intentionally small:

```yaml
watcher:
  enabled: false
  mode: systemd
```

Docker watcher mode may include an image:

```yaml
watcher:
  enabled: true
  mode: docker
  image: sftpwarden-watcher:local
```

`watcher.include` and `watcher.exclude` are not valid project config. The CLI derives watched files from context and provider configuration.

Provider paths and upload directories reject parent-directory traversal. Usernames are restricted to OpenSSH-safe account names.

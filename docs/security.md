# Security

SFTPWarden is designed for conservative SFTP operations, but it still runs on
your hosts and networks. Treat it as infrastructure.

## Defaults

- Users and secrets are not baked into Docker images.
- Plaintext passwords are rejected in provider data.
- `sftpwarden user add --password` hashes before writing provider data.
- Host keys, user data, and UID/GID state are persisted outside the image.
- Removed users are disabled; their data is not deleted.
- `.env`, `data/`, `state/`, `host_keys/`, Git metadata, and Python caches are not
  watched or synced by the watcher.
- SSH private keys for watcher containers should be mounted read-only.

## SSH Restrictions

The runtime disables:

- root login;
- empty passwords;
- TCP forwarding;
- agent forwarding;
- X11 forwarding;
- tunnels;
- user environments.

SFTP users are matched by group and forced into `internal-sftp`.

## Chroot Layout

Each user is isolated under:

```text
/data/<username>/
  upload/
```

Expected permissions:

```text
/data/<username>         root:root      755
/data/<username>/upload  <uid>:<gid>    750
```

OpenSSH requires the chroot directory itself to be owned by root and not writable
by the user.

## Key-Only Deployments

Add valid public keys for every active user, then disable password login:

```yaml
auth:
  allow_public_key: true
  allow_password: false
  recommended: public_key
```

## Limitations

OpenSSH chroot inside a container is useful isolation for SFTP workflows. It is
not a replacement for:

- host hardening;
- network firewalling;
- patch management;
- backups;
- log monitoring;
- secret management.

Expose SFTP only to the networks that need it.

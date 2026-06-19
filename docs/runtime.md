# Runtime

The runtime image is a small OpenSSH-based container. It installs only Python, SFTPWarden, OpenSSH server, `shadow` user-management tools, and `tini`.

## Startup Flow

1. Read `SFTPWARDEN_CONFIG`, defaulting to `/etc/sftpwarden/sftpwarden.yaml`.
2. Generate persistent host keys if missing.
3. Load provider users.
4. Allocate UID/GID values and persist mappings in `/var/lib/sftpwarden/state.json`.
5. Create or update system users.
6. Create chroot and upload directories.
7. Render authorized keys.
8. Start periodic sync.
9. Start `sshd` on container port `22`.

## Refresh

```bash
sftpwarden runtime refresh --config /etc/sftpwarden/sftpwarden.yaml
```

The CLI calls this through Docker Compose locally or SSH remotely:

```bash
sftpwarden refresh -c dev
sftpwarden refresh --all
```

## Isolation Layout

```text
/data/<username>/
  upload/
```

Permissions:

```text
/data/<username>         root:root      755
/data/<username>/upload  <uid>:<gid>    750
```

OpenSSH uses `ChrootDirectory /data/%u` and `ForceCommand internal-sftp`.


# Runtime

The runtime image is a small OpenSSH-based container. It installs only Python, SFTPWarden, OpenSSH server, `shadow` user-management tools, and `tini`.

## Startup Flow

1. Read `SFTPWARDEN_CONFIG`, defaulting to `/etc/sftpwarden/sftpwarden.yaml`.
2. Generate persistent host keys if missing.
3. Load provider users.
4. Validate that every active user has at least one enabled authentication method.
5. Render `sshd_config` from the current auth and isolation settings.
6. Allocate UID/GID values and persist mappings in `/var/lib/sftpwarden/state.json`.
7. Create or update system users.
8. Create chroot and upload directories.
9. Render authorized keys.
10. Start periodic sync.
11. Start `sshd` on container port `22`.

## State

Runtime state is stored outside the image:

```json
{
  "version": 2,
  "users": {
    "alice": {
      "uid": 10000,
      "gid": 10000,
      "disabled": false
    }
  },
  "fingerprint": "..."
}
```

Existing state files with the older `uid_map` shape are migrated on read.

UID/GID allocation preserves existing mappings unless explicit values are provided in the provider. Duplicate explicit UID/GID values fail before changes are applied.

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

Users without a password hash receive an impossible password hash, so password login is unavailable for that user while key-based access can still be configured. Removed users are disabled and retained in state; their data is not deleted.

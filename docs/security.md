# Security

SFTPWarden is designed for conservative SFTP operations, but it still runs on
your hosts and networks. Treat it as infrastructure.

## Defaults

- Users and secrets are not baked into Docker images.
- Plaintext passwords are rejected in provider data.
- `sftpwarden user create --password` hashes before writing provider data.
- Host keys, user data, and UID/GID state are persisted outside the image.
- Removed users are disabled; their data is not deleted.
- User data is deleted only with the explicit `sftpwarden user remove --delete-files`
  flag.
- `.env`, `data/`, `state/`, `host_keys/`, Git metadata, and Python caches are not
  watched or synced by the watcher.
- Production watcher installs should prefer a native host scheduler so SSH uses
  the host's normal `ssh-agent`, `~/.ssh/config`, known hosts, and default identity.
- Docker watcher mode requires an explicit dedicated SSH key; it never mounts
  the whole `~/.ssh` directory.
- Backup archives can contain sensitive config, DSNs, host keys, runtime state,
  and provider snapshots. Protect them like infrastructure secrets.

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

The container entrypoint also clamps the inherited `nofile` limit before OpenSSH
starts. Some container platforms expose very large open-file limits; keeping the
runtime limit bounded avoids `internal-sftp` chroot sessions spending excessive
time closing inherited descriptor ranges. The default limit is `65536`; override
`SFTPWARDEN_NOFILE_LIMIT` only when the runtime platform needs a different value.

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

In schema v2, disabled or expired keys are not written to `authorized_keys`.
Expiring keys include OpenSSH's UTC `expiry-time` option. A disabled user has no
installed key authorizations. Revocation applies to new authentications; see
[existing-session handling](named-keys.md#revocation-and-expiry).
The runtime writes only active keys and keeps OpenSSH `restrict` options on every
line. Schema v1 `public_keys` remains supported for simple key-only deployments.

Named key controls in schema v2 are operator-facing and auditable:

- key names must match `^[a-z][a-z0-9._-]{0,63}$`;
- fingerprints are derived from the public key and validated when present;
- duplicate key names and duplicate fingerprints are rejected per user;
- `sftpwarden user key rotate`, `expire`, `disable`, and `enable` update one key
  without copying raw keys between commands.

Privileged chroot/upload creation opens each directory relative to an already
open directory descriptor and refuses symlinks or non-directory replacements.
Ownership and permission changes operate on those descriptors. If an existing
upload path contains a symlink, refresh fails; move its content into a real
directory inside the chroot and retry. SFTPWarden does not follow that link and
change an outside directory's ownership. These are Linux container operations;
the operator CLI does not perform privileged account/filesystem changes on a
macOS or Windows workstation.

The runtime refuses to modify an existing account unless its name and UID/GID
match managed state. System-account collisions, primary-group GID conflicts and
duplicate identity mappings fail before user changes. Retiring a provider user
does not release its UID/GID while its state/data are retained. New allocations
also avoid installed system users/groups. Changing a retained identity requires
an explicit maintenance/data-ownership procedure; refresh refuses to silently
renumber it.

Keep `state.json` with the corresponding data when restarting or recovering.
If data contains an identity absent from state, restore the matching state
before refreshing. Do not delete state to force startup. New identity reservations
are saved before account creation so a failed provisioning pass can retry with
the same IDs; the applied fingerprint advances only after the complete pass.

New accounts are provisioned together with Shadow's database locks. The runtime
image gives the root-only `newusers` tool a PAM password stage that leaves its
new shadow entries locked; it does not change SSH authentication. Reconciliation
verifies those locks before adding SFTP group membership, then applies the final
encrypted credentials with `chpasswd`. Chroot directories and authorized keys
are prepared before account creation. An interrupted batch remains pending and
reuses its reserved identities on retry.

## Remote Watcher SSH

Use a native watcher mode for production when the host's SSH configuration,
default identity, agent, `ProxyJump`, or bastion rules matter. `auto` chooses
Windows Task Scheduler, macOS launchd, or the first available Linux scheduler
from systemd, OpenRC, runit, and supervisord. Docker watcher mode is intentionally
stricter: every watched remote context must define `--ssh-key` with an existing
dedicated deployment key. The Docker watcher mounts those keys read-only and
copies them to an internal temporary path with private permissions before opening
SSH connections.

## Kubernetes Security

Kubernetes deployments keep the same boundaries:

- DSNs, passwords, private keys, and host keys belong in Kubernetes Secrets.
- ConfigMaps are for non-secret deployment content. For YAML/CSV Kubernetes
  providers, generated manifests or Helm values can include the provider entries
  that will be copied into the provider PVC. Treat those files as operational
  material and avoid using YAML/CSV for sensitive production user state.
- SFTP data and runtime state use PVCs so restarts do not lose user files or
  UID/GID state.
- Host keys are loaded from a Secret from the start; do not commit real host keys
  to Git.
- SFTPWarden v1.3 runs one OpenSSH runtime pod per context. `replicas > 1` is
  reserved and rejected until shared storage, shared host keys, provider-safe
  refresh, and UID/GID consistency are implemented.
- Use PostgreSQL, MariaDB/MySQL, or MongoDB providers for production Kubernetes
  deployments. The runtime reads those databases directly, so user changes can
  be reconciled by the sync loop or `sftpwarden refresh` without embedding
  provider rows in manifests. SQLite is single-pod/lab only.

## Backups

`sftpwarden backup` excludes SFTP user data under `data/` by default, but it still
captures operational material that can be sensitive:

- `sftpwarden.yaml`, including DSNs or environment variable names;
- raw YAML/CSV/SQLite provider files when they are local;
- exported `provider/users.json`;
- `host_keys/`;
- runtime `state/`.

Use `--include-data` only when the actual SFTP files must be part of the archive.
Store backups encrypted or in a restricted location, and avoid attaching them to
issues or support tickets.

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

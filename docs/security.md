# Security

SFTPWarden follows conservative defaults for an SFTP gateway.

## Defaults

- No users or secrets are baked into the image.
- The generated `.dockerignore` excludes `.env`, `old/`, runtime data, state, and host keys.
- Password authentication is enabled by default.
- Plaintext passwords are rejected in provider data; `sftpwarden user add --password` hashes the value before saving it.
- SSH public key auth is optional and can be used for key-only deployments.
- User data is not deleted when a user is removed from the provider.
- Core workflows do not manage host firewall rules and do not require `sudo`.
- Remote watcher design does not require Docker socket access.
- SSH private keys should be mounted read-only when a Docker watcher is used.

## SSH Restrictions

The runtime disables root login, empty passwords, SSH forwarding, tunneling, X11 forwarding, gateway ports, and user-provided environments. SFTP users are matched by group and forced into `internal-sftp`.

## Limitations

Chroot isolation is not a substitute for host hardening. Operators should still patch hosts, restrict network exposure, monitor logs, back up persistent volumes, and use strong SSH key management.

Password authentication is the default path. For key-only deployments, set `auth.allow_password: false` after adding valid public keys for every active user.

## References

- Python packaging metadata follows the PyPA `pyproject.toml` specification.
- Dockerfile choices follow Docker build best practices for small, reproducible images.
- OpenSSH restrictions use documented `sshd_config` behavior for `ChrootDirectory` and `internal-sftp`.
- Password guidance follows OWASP password storage guidance: never store plaintext passwords.

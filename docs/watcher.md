# Watcher

`sftpwarden watch` syncs local config/provider files for remote `local-sync` contexts.

Watched files:

- `sftpwarden.yaml`
- `users.yaml`
- `users.yml`
- `users.csv`

Ignored paths include `.env`, `data/`, `state/`, `host_keys/`, `.git/`, and `__pycache__/`.


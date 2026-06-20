# Operations

This guide covers day-to-day commands for local and remote environments.

## Local Runtime

```bash
sftpwarden init dev --root ~/sftpwarden-dev --yes
cd ~/sftpwarden-dev
sftpwarden compose --write
docker compose up -d --build
sftpwarden doctor
```

Preview and apply user changes:

```bash
sftpwarden plan -c dev
sftpwarden refresh -c dev
```

## Remote Deploy

Remote local-sync:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical

sftpwarden deploy -c prod --dry-run
sftpwarden deploy -c prod --yes
```

Remote-only:

```bash
sftpwarden context add archive deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical

sftpwarden refresh -c archive --dry-run
```

Remote setup checks verify SSH connectivity and `docker compose version`.

## Watcher

`sftpwarden watch` is only for remote `local-sync` contexts. It syncs editable
config/provider files to remote hosts.

```bash
sftpwarden watcher status
sftpwarden watcher install --watcher systemd --dry-run
sftpwarden watcher install --watcher docker --yes
sftpwarden watcher uninstall --yes
```

Watched files are derived from the context registry and provider configuration.
Docker Compose changes require an explicit deploy.

Systemd watcher installation uses `sudo` for service setup and enables the service
with `systemctl enable --now`.

Docker watcher mode mounts the context registry, local project folders, and SSH
key material read-only. It does not require Docker socket access.

## Runtime State

Runtime state lives at `/var/lib/sftpwarden/state.json` inside the container and
should be backed by the `state/` volume.

Host keys live in `/etc/sftpwarden/host_keys` and should be backed by `host_keys/`
so server fingerprints do not change on restart.

## Troubleshooting

Runtime is not running:

```bash
docker compose ps
docker compose up -d
sftpwarden refresh -c dev
```

Remote checks fail:

```bash
ssh deploy@example.com true
ssh deploy@example.com 'docker compose version'
```

Provider data changed but users did not update:

```bash
sftpwarden plan -c dev
sftpwarden refresh -c dev --dry-run
sftpwarden refresh -c dev
```

# Operations

This guide covers day-to-day commands for local and remote environments.

## Local Runtime

```bash
mkdir -p ~/sftpwarden-dev
cd ~/sftpwarden-dev
sftpwarden init dev --yes
sftpwarden deploy
sftpwarden doctor
```

Preview and apply user changes:

```bash
sftpwarden plan
sftpwarden refresh
```

Check project and runtime health:

```bash
sftpwarden health
sftpwarden health --json
```

`sftpwarden init` sets the created context as active. The recommended workflow is
the Docker-style one: create a project directory, `cd` into it, initialize it, and
run commands without repeating `--context`. Use `sftpwarden context use dev` to
switch later, or pass `--context dev`/`-c dev` for one explicit command.

## Remote Deploy

Remote local-sync:

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical

sftpwarden deploy --dry-run
sftpwarden deploy --yes
```

Remote-only:

```bash
sftpwarden init archive --remote deploy@example.com:/opt/sftpwarden \
  --remote-only \
  --critical

sftpwarden refresh --dry-run
```

Remote setup checks verify SSH connectivity and `docker compose version`.
Local deploys also check Docker Compose before running `up -d --build`. If the
check fails, install Docker Compose v2 and retry `sftpwarden deploy`.

Use `sftpwarden context add` when the project already exists on the remote host and
you only need to register it locally:

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical
sftpwarden context use prod
```

## Watcher

`sftpwarden watch` is only for remote `local-sync` contexts. It syncs
YAML/CSV/SQLite user provider files to remote hosts. It does not sync
`sftpwarden.yaml`.

```bash
sftpwarden watcher status
sftpwarden watcher install --watcher systemd --dry-run
sftpwarden watcher install --watcher docker --yes
sftpwarden watcher uninstall --yes
```

Watched files are derived from the context registry and provider configuration.
Configuration and Docker Compose changes require an explicit deploy.

Systemd watcher installation uses `sudo` for service setup and enables the service
with `systemctl enable --now`. Use this mode for production when SSH should use
the host's default identity, agent, SSH config, bastions, or `ProxyJump`.

Docker watcher mode mounts the context registry, local project folders, and only
explicit dedicated SSH keys read-only. It does not mount `~/.ssh` and does not
require Docker socket access.

## Provider Transfer

Use provider transfer commands when you are moving users between storage backends,
creating a portable snapshot, or copying users from one context to another.

Export users:

```bash
sftpwarden provider export --format json > users.json
sftpwarden provider export --output users.yaml
```

Import users into the active context:

```bash
sftpwarden provider import --input users.json --merge
sftpwarden provider import --input users.yaml --replace --dry-run
```

Copy users between contexts:

```bash
sftpwarden provider copy \
  --from-context dev \
  --to-context prod \
  --merge \
  --dry-run
```

`--merge` upserts source users and keeps destination-only users. `--replace`
makes the destination exactly match the source. Provider transfer refreshes only
when runtime-relevant user fields change; comment-only changes do not trigger a
refresh.

## Backup and Restore

Create a project backup:

```bash
sftpwarden backup --output sftpwarden-prod.tar.gz --yes
```

Restore a backup:

```bash
sftpwarden restore sftpwarden-prod.tar.gz --yes
```

Backups include project config, Compose file, provider snapshot, raw local
provider files when available, host keys, and runtime state. SFTP user data under
`data/` is excluded unless you pass `--include-data`.

Backups may contain secrets if DSNs or environment references are stored in
`sftpwarden.yaml`. Store backup archives with the same care as infrastructure
secrets.

## Runtime State

Runtime state lives at `/var/lib/sftpwarden/state.json` inside the container and
should be backed by the `state/` volume.

Host keys live in `/etc/sftpwarden/host_keys` and should be backed by `host_keys/`
so server fingerprints do not change on restart.

## Deleting User Data

By default, removing a user removes the provider entry and disables access after
refresh. User files remain on disk:

```bash
sftpwarden user remove alice --yes
```

Use the explicit delete flag only when the data should be destroyed:

```bash
sftpwarden user remove alice --delete-files --yes
```

## Troubleshooting

Runtime is not running:

```bash
docker compose ps
sftpwarden deploy
sftpwarden refresh
```

Remote checks fail:

```bash
ssh deploy@example.com true
ssh deploy@example.com 'docker compose version'
```

If `docker compose version` fails locally or remotely, install Docker Compose v2
before running `sftpwarden deploy` again.

Provider data changed but users did not update:

```bash
sftpwarden plan
sftpwarden refresh --dry-run
sftpwarden refresh
```

Healthcheck fails in Docker Compose:

```bash
sftpwarden health --json
docker compose exec sftpwarden sftpwarden runtime health --json
```

The generated Docker Compose file uses `sftpwarden runtime health` as its
container healthcheck.

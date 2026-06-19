# SFTPWarden Implementation Plan

## 1. Summary

SFTPWarden is an open source, container-native SFTP gateway for DevOps and Platform Engineering use cases.

It provides:

- A lightweight Docker runtime based on OpenSSH.
- Per-user isolation with OpenSSH `ChrootDirectory`.
- User provisioning from YAML, CSV, MySQL, or PostgreSQL.
- Automatic UID/GID allocation unless explicitly provided.
- A professional Python CLI using Typer and Rich.
- Local, remote `local-sync`, and remote `remote-only` contexts.
- Global CLI defaults, including a default provider.
- Remote deployment through SSH, rsync, and Docker Compose.
- Automatic watcher management for remote `local-sync` contexts.
- A `refresh` command to force runtimes to reload users immediately.
- User management commands for listing, adding, updating, and removing users.
- English-only code, CLI text, logs, examples, and documentation.

A core product feature is that the runtime container stays small, simple, and resource-light.

## 2. Target Project Structure

The project should use a clean `src/` layout and keep implementation concerns separated. The current flat module layout may be migrated incrementally to this structure.

```text
sftpwarden/
  README.md
  LICENSE
  SECURITY.md
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  CHANGELOG.md
  pyproject.toml
  .gitignore
  .dockerignore
  .env.example

  .github/
    workflows/
      ci.yml
      docker.yml
      release.yml
      security.yml
    ISSUE_TEMPLATE/
      bug_report.yml
      feature_request.yml
    PULL_REQUEST_TEMPLATE.md
    dependabot.yml

  src/
    sftpwarden/
      __init__.py
      __main__.py
      cli.py
      console.py
      constants.py
      errors.py
      paths.py

      config/
        __init__.py
        global_config.py
        project_config.py
        schema.py
        defaults.py

      contexts/
        __init__.py
        models.py
        registry.py
        resolver.py
        remote_url.py
        critical.py

      providers/
        __init__.py
        base.py
        yaml_provider.py
        csv_provider.py
        mysql_provider.py
        postgres_provider.py
        mutations.py

      users/
        __init__.py
        models.py
        service.py
        validation.py

      runtime/
        __init__.py
        apply.py
        state.py
        uid_gid.py
        supervisor.py
        refresh.py
        sshd.py

      remote/
        __init__.py
        ssh.py
        rsync.py
        checks.py
        deploy.py

      watcher/
        __init__.py
        service.py
        systemd.py
        docker.py
        targets.py

      render/
        __init__.py
        compose.py
        sshd_config.py
        systemd.py

      security/
        __init__.py
        passwords.py
        keys.py
        permissions.py
        validation.py

  docker/
    runtime/
      Dockerfile
      entrypoint.sh
      sshd_config.template
    watcher/
      Dockerfile
      entrypoint.sh

  examples/
    yaml/
      sftpwarden.yaml
      users.yaml
      docker-compose.yml
    csv/
      sftpwarden.yaml
      users.csv
      docker-compose.yml
    mysql/
      sftpwarden.yaml
      schema.sql
      docker-compose.yml
    postgres/
      sftpwarden.yaml
      schema.sql
      docker-compose.yml
    remote-local-sync/
      README.md
    remote-only/
      README.md
    watcher-docker/
      docker-compose.yml

  docs/
    index.md
    installation.md
    getting-started.md
    configuration.md
    cli.md
    contexts.md
    remote-contexts.md
    watcher.md
    refresh.md
    users.md
    providers.md
    runtime.md
    isolation-model.md
    security.md
    operations.md
    troubleshooting.md
    architecture.md
    roadmap.md

  tests/
    unit/
      test_config.py
      test_contexts.py
      test_providers.py
      test_users.py
      test_watcher.py
      test_refresh.py
      test_runtime_plan.py
    integration/
      test_cli.py
      test_docker_runtime.py
      test_remote_contexts.py
      test_user_flows.py
```

Notes:

- The runtime image and watcher image should have separate Dockerfiles.
- The top-level `Dockerfile` may remain as a compatibility entrypoint, but the canonical runtime Dockerfile should live under `docker/runtime/`.
- The current `.github/workflows/ci.yml` should be kept and expanded; it currently has Python `3.12` twice and should include `3.13` instead.
- Documentation should be broad enough for the README to feel complete, but deep operational details should live in `docs/`.

## 3. Core Decisions

- Project name: `SFTPWarden`
- CLI command: `sftpwarden`
- Python package: `sftpwarden`
- License: Apache-2.0
- Runtime: lightweight Docker image + OpenSSH
- Runtime container SSH port: always `22`
- Host-facing SFTP port: configured with `server.port`
- CLI framework: Typer
- Terminal output: Rich only
- CLI home: `~/.sftpwarden`
- Global config: `~/.sftpwarden/config.toml`
- Context registry: `~/.sftpwarden/contexts.toml`
- Default local root: `~/sftpwarden`
- Default remote root: `~/sftpwarden`
- Built-in fallback provider: `yaml`
- `sftpwarden watch`: local-to-remote file watcher for remote `local-sync` contexts only
- `sftpwarden refresh`: immediate runtime user reload for active, specified, or all contexts
- Kubernetes/Helm: planned for v1.1, not v1.0

## 4. Global Configuration And Defaults

Global config:

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

Provider selection precedence:

1. `--provider`
2. `SFTPWARDEN_DEFAULT_PROVIDER`
3. `~/.sftpwarden/config.toml.default_provider`
4. built-in fallback `yaml`

First `sftpwarden init` behavior:

- If `--provider` is passed and no global provider exists, set that provider as the global default and tell the user.
- If `--provider` is not passed and no global provider exists, prompt the user to choose one.
- If a global provider already exists and `--provider` is omitted, print which default provider is being used without prompting.
- Changing the global provider later never mutates existing contexts or existing `sftpwarden.yaml` files.

Defaults and prompting rules:

- If `--root` is omitted for local or local-sync contexts, default to `~/sftpwarden` and ask whether to use it or enter another path.
- If `--remote-root` is omitted for remote contexts, default to `~/sftpwarden` and ask whether to use it or enter another path.
- If a remote URL omits the path, use the default remote root and ask for confirmation.
- If `--port` is omitted, use `22` and print that default without prompting.
- If `--provider` is omitted and a global default exists, use it and print it without prompting.
- If `--watcher` is omitted and a watcher already exists, use the existing watcher and print it without prompting.
- If the context name looks production-like, such as `prod`, `production`, `prd`, `live`, or `main`, and `--critical` is not passed, ask the user to confirm creating it as non-critical.

## 5. CLI Interface

Primary commands:

```bash
sftpwarden init
sftpwarden init <context-name>
sftpwarden init remote

sftpwarden info
sftpwarden validate
sftpwarden plan
sftpwarden refresh
sftpwarden sync
sftpwarden deploy
sftpwarden watch
sftpwarden compose
sftpwarden doctor

sftpwarden config show
sftpwarden config default-provider

sftpwarden context add
sftpwarden context ls
sftpwarden context current
sftpwarden context default
sftpwarden context use
sftpwarden context clear
sftpwarden context show
sftpwarden context remove
sftpwarden context rename

sftpwarden watcher status
sftpwarden watcher install
sftpwarden watcher uninstall

sftpwarden users
sftpwarden user add
sftpwarden user remove
sftpwarden user update
sftpwarden user show
```

Local init UX:

```bash
sftpwarden init
```

Prompts for the context/project name.

```bash
sftpwarden init dev
```

Initializes the project and registers the context as `dev` without asking for the name.

Rules:

- The context name becomes `project.name` in `sftpwarden.yaml`.
- `sftpwarden init --context dev` may be supported as a compatibility alias, but the preferred UX is `sftpwarden init dev`.
- `-c` always means `--context`.
- Config paths use `--config`.
- Human output uses Rich.
- Machine output uses `--json`.
- Errors include a practical fix suggestion.

Common flags:

```bash
--context / -c
--config
--provider
--root
--host
--user
--port
--remote-root
--ssh-key
--critical
--remote-only
--watcher systemd
--watcher docker
--include-env
--skip-checks
--all
--yes / -y
--dry-run
--json
```

## 6. Context Model

Supported context types:

| Type | Created by | Storage | Watcher | Refresh |
|---|---|---|---|---|
| Local new | `sftpwarden init` or `sftpwarden init dev` | local files | none | yes |
| Local existing | `sftpwarden context add dev` | local files | none | yes |
| Remote local-sync | `sftpwarden context add prod user@host:/dir` | local source synced to remote | required | yes |
| Remote-only | `sftpwarden context add prod user@host:/dir --remote-only` | remote files only | none | yes |

Context registry example:

```toml
default = "dev"

[contexts.dev]
name = "dev"
type = "local"
root = "/Users/nacho/sftpwarden"
config = "/Users/nacho/sftpwarden/sftpwarden.yaml"
provider = "yaml"
critical = false

[contexts.prod]
name = "prod"
type = "remote"
storage = "local-sync"
root = "/Users/nacho/sftpwarden-prod"
config = "/Users/nacho/sftpwarden-prod/sftpwarden.yaml"
provider = "yaml"
critical = true
watcher_required = true

[contexts.prod.remote]
host = "sftp-prod.example.com"
user = "deploy"
port = 22
remote_root = "/opt/sftpwarden"
remote_config = "/opt/sftpwarden/sftpwarden.yaml"
ssh_key = "~/.ssh/id_ed25519"
compose_file = "docker-compose.yml"

[contexts.archive]
name = "archive"
type = "remote"
storage = "remote-only"
root = ""
config = ""
provider = "csv"
critical = true
watcher_required = false

[contexts.archive.remote]
host = "sftp-archive.example.com"
user = "deploy"
port = 22
remote_root = "/opt/sftpwarden"
remote_config = "/opt/sftpwarden/sftpwarden.yaml"
ssh_key = "~/.ssh/id_ed25519"
compose_file = "docker-compose.yml"
```

Remote-only clarification:

- Top-level `root` and `config` stay empty.
- Remote-only paths live under `[contexts.<name>.remote]`.
- CLI operations execute through SSH against `remote.remote_root` and `remote.remote_config`.
- No watcher is required because changes happen directly on the remote server or are applied through `refresh`.

Context resolution order:

1. `--config`
2. `--context/-c`
3. `SFTPWARDEN_CONTEXT`
4. persistent default from `~/.sftpwarden/contexts.toml`
5. `sftpwarden.yaml` in current directory
6. clear error with suggested commands

## 7. Context Creation

### New Local Context

```bash
sftpwarden init
sftpwarden init dev
```

Behavior:

- Creates local project files.
- Uses `--root` or asks about default `~/sftpwarden`.
- Resolves provider from precedence.
- Creates provider file or provider sample.
- Writes `project.name` as the context name.
- Registers context.
- Does not create or mention watcher.

### Existing Local Context

```bash
cd ~/copied-sftpwarden-project
sftpwarden context add dev
```

Behavior:

- Requires `sftpwarden.yaml` in current directory.
- Validates the project.
- Registers it as a local context.
- Reads provider and project name from `sftpwarden.yaml`.
- Does not create or overwrite files.
- Does not create or mention watcher.

### Remote Local-Sync Context

```bash
sftpwarden context add prod deploy@sftp-prod.example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical
```

Behavior:

- Creates remote context with `storage = "local-sync"`.
- Local files are source of truth.
- Verifies SSH connectivity.
- Verifies remote Docker Compose.
- Requires watcher support.
- If watcher is already installed, reuse it and do not reinstall.
- If no watcher exists, install one automatically using resolved watcher mode.
- If an explicit `--watcher` differs from installed watcher, ask whether to replace existing watcher or keep it.

### Remote-Only Context

```bash
sftpwarden context add archive deploy@sftp-archive.example.com:/opt/sftpwarden \
  --remote-only
```

Behavior:

- Creates remote context with `storage = "remote-only"`.
- Stores remote paths only under `[contexts.<name>.remote]`.
- Leaves top-level `root = ""` and `config = ""`.
- Verifies SSH and Docker Compose.
- Does not install watcher.
- Does not ask about watcher.

### Remote URL Syntax

Supported:

```text
[user@]host[:/remote/path]
```

Rules:

- If path is omitted, use the default remote root and ask for confirmation.
- If URL user and `--user` are both provided, they must match.
- If URL path and `--remote-root` are both provided, they must match.
- Conflicts fail with a clear Rich error.

## 8. Watcher Management

Watcher is required only for:

```text
context.type = remote
context.storage = local-sync
```

Watcher is never required for:

```text
context.type = local
context.storage = remote-only
```

Automatic watcher behavior:

1. Check watcher status.
2. If no watcher exists, install watcher automatically.
3. If watcher exists and no explicit different watcher was requested, reuse it.
4. If watcher exists and requested watcher mode differs, ask whether to replace it, keep it, or cancel.
5. Store watcher metadata globally.
6. Do not create duplicate watchers.

`sftpwarden watcher install` is only for replacing or manually restoring watcher setup.

Behavior:

- If no watcher exists, install selected watcher.
- If the same watcher already exists, print no-op.
- If a different watcher exists, warn that it will be modified.
- Ask confirmation before uninstalling existing watcher and installing the new one.
- With `--yes`, allow non-interactive replacement.

`sftpwarden watch` behavior:

- Reads `~/.sftpwarden/contexts.toml`.
- Finds all remote local-sync contexts.
- Ignores local contexts.
- Ignores remote-only contexts.
- Determines watched files from each context and each context’s `sftpwarden.yaml`.
- Watches only config and provider user files.
- Syncs changed files to corresponding remote hosts.
- Runs in foreground when called directly.
- Used by systemd and Docker watcher.

Files watched/synced:

```text
sftpwarden.yaml
users.yaml
users.csv
provider-specific user/config files
```

Files not watched:

```text
docker-compose.yml
.env
data/
state/
host_keys/
.git/
__pycache__/
```

Rationale:

- Docker Compose changes require an explicit deploy.
- The watcher exists only for user/config changes that should be reflected by the remote runtime.

Systemd watcher:

- If watcher mode is `systemd`, no Docker image is needed in `sftpwarden.yaml`.
- The systemd user service runs `sftpwarden watch`.
- It must not require `sudo`.

Docker watcher:

- If watcher mode is `docker`, Docker Compose includes a watcher service.
- The Docker watcher runs `sftpwarden watch`.
- The Docker watcher must have access to the context registry, relevant local project folders, and SSH key material mounted read-only.
- The Docker watcher must not require Docker socket access.

## 9. Refresh Command

`refresh` is different from `watch`.

- `watch` keeps local provider/config files synced to remote local-sync contexts.
- `refresh` tells one or more SFTPWarden runtimes to immediately reload users from their provider.

Commands:

```bash
sftpwarden refresh
sftpwarden refresh -c prod
sftpwarden refresh --all
```

Behavior by context type:

| Context type | Refresh behavior |
|---|---|
| Local | Execute refresh against local Docker Compose runtime |
| Remote local-sync | Sync local provider changes to remote, then execute refresh against remote Docker runtime |
| Remote-only | Execute refresh against remote Docker runtime |
| `--all` | Refresh all registered contexts |

Runtime requirement:

```bash
sftpwarden runtime refresh
```

Recommended CLI implementation:

- Local: `docker compose exec sftpwarden sftpwarden runtime refresh`
- Remote: SSH into `remote_root`, then run the equivalent Docker Compose command.

If the runtime is not running, show a clear error and suggest `sftpwarden deploy` or `docker compose up -d`.

## 10. User Management Commands

Commands:

```bash
sftpwarden users
sftpwarden user show <username>
sftpwarden user add <username>
sftpwarden user update <username>
sftpwarden user remove <username>
```

Rules:

- All important values can be passed by flags.
- Missing values are prompted interactively.
- YAML/CSV providers can be mutated directly.
- SQL providers require a configured write strategy; otherwise mutations fail with a clear message.
- After successful user add/update/remove, run `sftpwarden refresh -c <context>` automatically when possible.
- User data is not deleted when a user is removed from the provider.

Example:

```bash
sftpwarden user add alice \
  --public-key "ssh-ed25519 AAAA..." \
  --upload-dir upload
```

## 11. `sftpwarden.yaml` Configuration

Complete example for systemd watcher mode:

```yaml
version: 1

project:
  name: dev
  description: "SFTPWarden environment"

server:
  host: "0.0.0.0"
  port: 2222
  data_dir: /data
  host_keys_dir: /etc/sftpwarden/host_keys
  state_dir: /var/lib/sftpwarden
  group: sftpwarden_users

sync:
  enabled: true
  interval_seconds: 60
  apply_on_startup: true
  disable_missing_users: true
  delete_missing_user_data: false

auth:
  allow_public_key: true
  allow_password: true
  recommended: public_key
  password_hash_scheme: yescrypt

isolation:
  mode: chroot
  upload_dir: upload
  root_owner: root
  root_group: root
  root_permissions: "755"
  upload_permissions: "750"

uid_gid:
  mode: auto
  start: 10000
  end: 60000
  preserve_existing: true

provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml

logging:
  level: info
  format: json

docker:
  image: ghcr.io/<user>/sftpwarden:latest
  container_name: sftpwarden
  restart: unless-stopped
  compose_file: docker-compose.yml

remote:
  enabled: false
  storage: local-sync
  host: null
  user: null
  port: 22
  remote_root: null
  remote_config: null
  ssh_key: null
  delete_extra_files: false
  include_env: false

watcher:
  enabled: false
  mode: systemd
```

Docker watcher mode:

```yaml
watcher:
  enabled: true
  mode: docker
  image: ghcr.io/<user>/sftpwarden-watcher:latest
```

Rules:

- `server.container_port` must not exist; container SSH port is always `22`.
- `server.port` is the host port exposed by Docker.
- If `watcher.mode = systemd`, no watcher image is needed.
- `watcher.include` and `watcher.exclude` must not be part of `sftpwarden.yaml`.
- Watcher file selection is derived from context registry and provider configuration.

Minimum valid config:

```yaml
version: 1

project:
  name: sftpwarden

provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
```

Recommendation:

- Users should normally run `sftpwarden init` rather than hand-writing `sftpwarden.yaml`.
- `init` generates the full config with documented defaults so users can edit it safely afterward.

## 12. Providers

Supported v1 providers:

| Provider | Read users | Mutate users | Intended use |
|---|---:|---:|---|
| YAML | yes | yes | quickstart, GitOps-like workflows |
| CSV | yes | yes | simple imports and business-managed lists |
| MySQL | yes | write strategy required | existing database integration |
| PostgreSQL | yes | write strategy required | production database integration |

Provider files:

- YAML provider uses `users.yaml`.
- CSV provider uses `users.csv`.
- SQL providers use `url` and `query`.
- Environment variables in provider URLs must be expanded at runtime.

SQL write strategy:

- v1 may ship read-only SQL providers.
- If SQL mutation is not configured, `sftpwarden user add/update/remove` must fail with a clear message.
- A future write strategy can define insert/update/delete statements in config.

## 13. Docker Runtime

The runtime image must be lightweight and update users without rebuilding the image.

Runtime image goals:

- Minimal base image.
- Only required OpenSSH and runtime dependencies.
- No build tools or package caches in the final image.
- No unnecessary docs, shells, or development utilities.
- Low idle CPU and memory usage.
- Clear documentation of lightweight resource usage as a feature.

Startup flow:

1. Read `SFTPWARDEN_CONFIG`, defaulting to `/etc/sftpwarden/sftpwarden.yaml`.
2. Load provider.
3. Validate users.
4. Assign UID/GID.
5. Create/update/disable users.
6. Create directories.
7. Apply permissions.
8. Render auth files.
9. Start OpenSSH.

Periodic sync:

1. Sleep for configured interval.
2. Load provider.
3. Compute desired-state fingerprint.
4. If unchanged, do nothing.
5. If changed, generate plan.
6. Apply create/update/disable actions.
7. Log result.

Immediate refresh:

- Runtime supports `sftpwarden runtime refresh`.
- It performs one immediate provider reload and apply cycle.
- It is used by `sftpwarden refresh`.

## 14. User Isolation And UID/GID

Each user gets:

```text
/data/<username>/
  upload/
```

Permissions:

```text
/data/<username>           root:root      755
/data/<username>/upload    <uid>:<gid>    750
```

OpenSSH restrictions:

```text
Subsystem sftp internal-sftp

Match Group sftpwarden_users
  ChrootDirectory /data/%u
  ForceCommand internal-sftp
  PasswordAuthentication yes
  PubkeyAuthentication yes
  PermitTunnel no
  AllowAgentForwarding no
  AllowTcpForwarding no
  X11Forwarding no
```

UID/GID rules:

- If `uid` is missing, allocate one automatically.
- If `gid` is missing, use the user’s UID.
- Explicit `uid`/`gid` values are respected.
- Automatic mappings are persisted in `/var/lib/sftpwarden/state.json`.
- Conflicting IDs fail validation before applying changes.

Security guarantee:

> SFTPWarden provides OpenSSH chroot isolation inside a container. It does not provide VM-grade isolation and does not replace host hardening.

## 15. Remote Operations

Remote context creation must check:

```bash
ssh <user>@<host> true
ssh <user>@<host> 'docker compose version'
```

Remote deploy for local-sync contexts:

1. Resolve context.
2. Validate local config.
3. Check SSH and Docker Compose.
4. Generate deploy plan.
5. Ask confirmation if context is critical.
6. Create remote root if needed.
7. Rsync required files.
8. Run `docker compose pull`.
9. Run `docker compose up -d`.
10. Run health check.

Remote deploy for remote-only contexts:

1. Resolve context.
2. Check SSH and Docker Compose.
3. Validate remote files.
4. Ask confirmation if context is critical.
5. Run `docker compose pull`.
6. Run `docker compose up -d`.
7. Run health check.

Never sync by default:

```text
.env
data/
state/
host_keys/
.git/
__pycache__/
```

`.env` is synced only with `--include-env`.

## 16. Security Rules

- Do not bake users or secrets into Docker images.
- Do not recommend plaintext passwords for production.
- Do not print secrets in logs.
- Do not sync `.env` unless explicitly requested.
- Do not delete user data when users are removed.
- Do not manage host firewall rules in v1.
- Do not use `sudo` in the core workflow.
- Watcher systemd service must be a user service.
- Watcher replacement must require confirmation unless `--yes`.
- Docker watcher must not require Docker socket access.
- SSH keys are mounted read-only.
- Disable shell access and SSH forwarding for SFTP users.
- Validate path traversal for usernames, upload directories, provider paths, and remote paths.
- Persist host keys to avoid changing server fingerprints on restart.

## 17. Documentation Standard

All docs and code must be English-only.

README must include:

- project summary;
- lightweight runtime/container positioning;
- clickable Table of Contents;
- first-run global provider setup;
- `sftpwarden init` and `sftpwarden init dev` usage;
- local quickstart;
- remote local-sync quickstart;
- remote-only quickstart;
- watcher behavior;
- refresh command;
- user management commands;
- provider matrix;
- context workflows;
- isolation summary;
- security summary;
- links to detailed docs.

Detailed docs must explain:

- difference between `watch` and `refresh`;
- global config;
- context registry;
- default paths and prompts;
- critical context detection;
- watcher lifecycle;
- user commands;
- provider behavior;
- remote-only behavior;
- lightweight image design;
- security limitations;
- troubleshooting.

Documentation quality rules:

- Professional, concise, and practical.
- No generated-sounding filler.
- Commands must be copy-pasteable.
- Config examples must validate in CI.
- Tables of Contents should be clickable.

## 18. CI/CD And Release

Required workflows:

- `ci.yml`: lint, format check, tests, docs validation.
- `docker.yml`: build runtime image, build watcher image, smoke test runtime.
- `release.yml`: publish PyPI package, publish GHCR images, create GitHub Release.
- `security.yml`: dependency scan, container scan, SBOM, OpenSSF Scorecard.

CI improvements needed:

- Use Python `3.11`, `3.12`, and `3.13`; avoid duplicated `3.12`.
- Install optional extras needed by tests.
- Validate generated Compose files.
- Validate `sftpwarden.yaml` examples.
- Add a Docker smoke test that verifies the runtime starts and listens on port 22 internally.
- Add a chroot isolation integration test when feasible.

Release rules:

- Follow Semantic Versioning.
- Maintain `CHANGELOG.md` using Keep a Changelog style.
- Runtime image: `ghcr.io/<user>/sftpwarden:<version>`.
- Watcher image: `ghcr.io/<user>/sftpwarden-watcher:<version>`.
- Also publish `latest` for stable releases only.

## 19. Test Plan

Unit tests:

- `sftpwarden init` prompts for context name.
- `sftpwarden init dev` uses `dev` as context and `project.name`.
- `server.container_port` is rejected if present.
- Minimum config requires `project.name`.
- Watcher schema rejects `include` and `exclude`.
- Watcher schema allows image only for Docker watcher mode.
- `watch` derives watched files from contexts and provider config.
- `watch` ignores Docker Compose changes.
- Remote-only context stores top-level `root` and `config` as empty.
- `refresh` resolves active/specified/all contexts.
- User add/update/remove triggers refresh.
- Provider path validation prevents traversal.
- Remote URL parser handles missing user and missing path.
- Production-like context names trigger critical confirmation.
- SQL mutation fails cleanly without write strategy.

Integration tests:

- `sftpwarden init` interactive flow creates valid config.
- `sftpwarden init dev` creates `project.name = dev`.
- Generated `sftpwarden.yaml` does not include `container_port`.
- Generated systemd watcher config does not include image/include/exclude.
- Docker watcher config includes watcher image only when mode is docker.
- `sftpwarden watch` syncs only provider/config files.
- `sftpwarden refresh -c dev` forces local runtime reload.
- `sftpwarden refresh --all` refreshes all contexts.
- Docker runtime starts and stays idle with low process count.
- Docker runtime applies provider changes.
- Users cannot escape chroot.
- Removed users cannot log in but their data remains.

Documentation tests:

- README quickstart works.
- Docs explain `sftpwarden init` and `sftpwarden init dev`.
- Docs describe lightweight container design.
- Docs explain no `container_port` setting.
- Docs explain watcher config differences between systemd and Docker.
- Docs explain `watch` vs `refresh`.
- Full config example validates.
- Minimum config validates.
- All docs are English.

## 20. Roadmap

### v0.1 - Foundation

- Add package structure, Apache-2.0, `pyproject.toml`.
- Add Typer + Rich CLI.
- Add global config and provider defaults.
- Add context registry.
- Add `sftpwarden init` and `sftpwarden init <context-name>`.
- Add local existing context registration.
- Add remote URL parser.
- Add initial config schema.
- Add README and core docs.

### v0.2 - Lightweight Docker Runtime

- Add lightweight Dockerfile and OpenSSH runtime.
- Add startup sync.
- Add runtime refresh command.
- Add chroot isolation.
- Add UID/GID allocation.
- Add persistent runtime state.
- Document lightweight image positioning.

### v0.3 - Runtime Sync

- Add provider polling.
- Add desired-state fingerprinting.
- Add create/update/disable sync actions.
- Add CLI `refresh` and `refresh --all`.

### v0.4 - Remote Contexts

- Add remote context add and init remote.
- Add SSH and Docker Compose checks.
- Add local-sync and remote-only modes.
- Add deploy.
- Add critical context confirmation.

### v0.5 - Watchers

- Add `sftpwarden watch`.
- Add automatic watcher setup for remote local-sync.
- Add systemd user watcher.
- Add Docker watcher.
- Add watcher install/uninstall/status.
- Add watcher replacement workflow.

### v0.6 - Providers And Users

- Add CSV, MySQL, PostgreSQL providers.
- Add user management commands.
- Add provider mutation support for YAML/CSV.
- Add automatic refresh after user commands.
- Define SQL write strategy or unsupported mutation behavior.

### v0.7 - CLI Completion

- Add validate, plan, compose, doctor.
- Complete context, config, watcher, remote, users, user, and refresh commands.
- Add JSON output where useful.

### v1.0 - Public Release

- Publish PyPI package.
- Publish lightweight runtime image and watcher image to GHCR.
- Complete documentation.
- Add CI/CD, security scanning, SBOM, and OpenSSF Scorecard.
- Validate quickstarts.

### v1.1 - Kubernetes

- Add Helm chart and Kubernetes manifests.
- Add ConfigMap/Secret/PVC examples.
- Add liveness/readiness probes.

## 21. Acceptance Criteria

SFTPWarden v1 is ready when:

- All code and docs are English-only.
- README is professional, has a clickable TOC, and links to deeper docs.
- The target project structure is either implemented or consciously mapped to current module names.
- `sftpwarden init` prompts for context name.
- `sftpwarden init dev` creates a context named `dev`.
- `project.name` is required in minimum config.
- `server.container_port` is not part of the config.
- Runtime container always uses SSH port `22` internally.
- Host port is configured with `server.port`.
- Systemd watcher config does not require Docker image/include/exclude fields.
- Docker watcher config uses image but no include/exclude lists.
- `watch` derives files to monitor from context/provider config.
- `watch` does not monitor Docker Compose changes.
- Docker runtime is lightweight and documented as such.
- `watch` works only for remote local-sync contexts.
- `refresh` forces immediate runtime user reload for active/specified/all contexts.
- User add/update/remove automatically triggers refresh.
- Local, remote local-sync, and remote-only refresh flows work.
- Remote local-sync context creation installs or reuses watcher automatically.
- Local and remote-only contexts never trigger watcher setup.
- Docker runtime applies provider changes periodically and on refresh.
- UID/GID defaults and overrides work.
- Users are isolated with chroot.
- YAML, CSV, MySQL, and PostgreSQL providers work.
- Package and Docker images are published.
- CI validates code, docs, Docker, watcher image, refresh behavior, and security checks.

## 22. Assumptions

- Preferred local init UX is `sftpwarden init` or `sftpwarden init <context-name>`.
- `sftpwarden init --context dev` may exist only as compatibility syntax.
- `-c` means `--context`.
- Config paths use `--config`.
- Global config lives in `~/.sftpwarden/config.toml`.
- Context registry lives in `~/.sftpwarden/contexts.toml`.
- Remote-only contexts keep top-level `root` and `config` empty by design.
- `watch` is for local-to-remote provider/config file sync only.
- `refresh` is for forcing runtime reloads.
- `.env`, `data/`, `state/`, and `host_keys/` are not synced remotely by default.
- Kubernetes is planned for v1.1.

## 23. Important Gaps To Close

These are the highest-impact items missing or not fully specified in the current implementation plan:

- Decide whether to migrate from flat modules like `src/sftpwarden/config.py` to the target package structure, or keep flat modules and document the mapping.
- Add explicit runtime state format for `/var/lib/sftpwarden/state.json`.
- Define how disabled users are represented in system accounts.
- Define host key generation and persistence behavior.
- Define SQL write strategy format or explicitly mark SQL mutation as post-v1.
- Add Docker watcher Compose generation details.
- Add real remote integration tests or a mock SSH/rsync test harness.
- Add release workflow details for PyPI and GHCR.
- Add docs validation to CI.
- Add SBOM and container security scan workflow.
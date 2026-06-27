# AGENTS.md

This file is the working guide for AI coding agents in this repository. Keep it
short, operational, and current. Prefer linking to deeper docs over duplicating
them here.

## Project Intent

SFTPWarden is a CLI-first, container-native SFTP management tool. It runs a small
OpenSSH runtime in Docker, keeps users and operational state outside the image,
and lets operators manage environments through declarative providers and
Docker-style contexts.

Core product principles:

- lightweight runtime, no mandatory control plane;
- predictable CLI behavior with Rich output and JSON modes where useful;
- explicit separation between user refresh, config deploy, watcher sync, and
  backup/restore operations;
- conservative security defaults for chroot, host keys, secrets, and user data.

## Repository Map

- `sftpwarden/cli.py` assembles the Typer app and installs global CLI error
  handling.
- `sftpwarden/cli_commands/` contains command modules only: argument handling,
  prompts, confirmation, and presentation.
- `sftpwarden/services/` contains CLI-facing business workflows.
- `sftpwarden/config/` contains project and global configuration models.
- `sftpwarden/contexts/` owns the context registry and local/remote context
  resolution.
- `sftpwarden/providers/` owns provider abstractions, registry, YAML/CSV/SQLite,
  MySQL/MariaDB, PostgreSQL, and MongoDB providers.
- `sftpwarden/runtime/` owns container-side Linux user, UID/GID, sshd, state, and
  sync logic.
- `sftpwarden/refresh/` triggers runtime refreshes locally or over SSH.
- `sftpwarden/watcher/` owns remote local-sync watcher planning and execution.
- `sftpwarden/remote/`, `render/`, `system/`, `security/`, `users/`, and `utils/`
  are focused support packages.
- `docker/runtime/` and `docker/watcher/` build the two official container images.
- `docs/`, `README.md`, `CHANGELOG.md`, and `CONTRIBUTING.md` are public-facing
  documentation.

## Architectural Rules

- Keep public CLI commands, option names, config formats, provider data formats,
  SQL schemas, and runtime state backward compatible unless a breaking change is
  explicitly requested and documented.
- Keep Typer commands thin. Put reusable behavior in `services/`, provider
  modules, runtime modules, or focused utilities.
- Do not add heavy architecture patterns without a concrete maintenance benefit.
  Prefer explicit functions and small services over framework-style indirection.
- Provider changes must go through the provider base/registry model. New providers
  should declare capabilities clearly and preserve read/write semantics.
- `refresh` applies user/provider changes. Config, Compose, context, and remote
  deployment changes require an explicit `deploy`.
- The watcher only syncs editable user provider files for remote `local-sync`
  contexts. It must not watch or apply config changes.
- `comment` is metadata and must not trigger runtime refresh by itself.
- Runtime code must stay usable inside the OpenSSH container with minimal
  dependencies.

## Commands Agents Should Use

Install for development:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
```

Main validation:

```bash
tox
```

Focused validation:

```bash
tox -e lint
tox -e py311
tox -e py312
tox -e py313
tox -e py314
tox -e coverage
tox -e docs
tox -e package
tox -e audit
tox -e clean
```

Useful direct checks while iterating:

```bash
python -m pytest tests/test_cli.py
sftpwarden validate --config examples/yaml/sftpwarden.yaml
sftpwarden compose --config examples/yaml/sftpwarden.yaml
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
```

## Testing Expectations

- Add focused tests for every behavior change.
- Keep coverage meaningful. Do not add opaque tests only to satisfy coverage.
- Use `tox` as the final validation for code changes.
- Use `tox -e docs` for docs-only changes.
- For Docker/runtime/watcher changes, include local Docker build or smoke-test
  validation when feasible.
- Preserve the explicit coverage rule in `pyproject.toml`; do not lower it.

## Code Style

- Code, comments, CLI output, docs, and examples are written in English.
- Use type hints for public functions and data structures.
- Use NumPy-style docstrings where docstrings add real value.
- Use `rg` for searching.
- Use `apply_patch` for manual file edits.
- Keep modules focused. Do not place CLI rendering, prompts, subprocess helpers,
  and business logic in the same file.
- Keep generated files, caches, local env files, build artifacts, and secrets out
  of commits.

## Security Boundaries

- Never print, commit, or invent secrets, private keys, tokens, customer data, or
  real DSNs.
- Treat backup archives as sensitive because they may include config, provider
  snapshots, host keys, and runtime state.
- Do not weaken chroot-oriented permissions, host-key persistence, plaintext
  password rejection, or explicit user-data deletion safeguards.
- Production watcher behavior should prefer systemd so SSH uses the host's normal
  SSH config and agent. Docker watcher mode must require explicit deployment keys.
- Remote commands and rsync/SSH paths must be quoted safely.

## Documentation And Release Rules

- README is the adoption path: keep it concise and user-oriented.
- Put deeper operational details in `docs/`.
- CLI behavior changes require updates to `docs/cli-reference.md` when user-facing.
- Config changes require docs and examples.
- When publishing a version, remove the shipped version from the README roadmap
  and list only the next two future versions. Use `CHANGELOG.md` for released
  versions and the longer future roadmap.
- SFTPWarden is licensed under MIT. Keep package metadata, `LICENSE`, and docs
  consistent with that.

## Branch And PR Conventions

- `dev` is the integration branch.
- `main` is protected and used for production/release promotion.
- Normal PRs target `dev`, not `main`.
- Keep PR summaries user-facing. Mention validation performed and compatibility
  risks.
- Do not rewrite unrelated user changes in a dirty worktree.

## Agent Operating Rules

- Read the relevant code before editing.
- Prefer the existing architecture over new abstractions.
- Make small, reviewable changes with tests.
- Keep public behavior stable unless the task explicitly asks to change it.
- If a command can mutate real systems, prefer dry-run tests or mocked command
  runners.
- If changing imports or moving modules, run lint, targeted tests, and coverage.

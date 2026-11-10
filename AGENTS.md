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
- Normal management upgrades defer server changes to deploy. Incompatible watcher
  updates require explicit all-context deployment consent before any mutation.
- Keep `release_compatibility.json` aligned with tested runtime and watcher bounds;
  never infer backward compatibility from package version numbers alone.
- `comment` is metadata and must not trigger runtime refresh by itself.
- Runtime code must stay usable inside the OpenSSH container with minimal
  dependencies.

## Operational Guarantees

- Never interpret a missing, unreadable, or malformed provider as an empty user
  set. Preserve the last applied state and report the failure.
- Revocation must remove authorization for new sessions, including the last
  usable credential. Do not leave old access active because a user has no usable
  credentials remaining. Expiry must work without another provider edit.
- Advance applied fingerprints and delivery checkpoints only after the required
  effects succeed. Partial operations must not be reported as complete.
- Health must reflect required process availability and synchronization progress,
  not just the existence of directories and configuration files.
- Protect read-modify-write operations against concurrent writers. Publish files
  atomically where required and verify visibility through actual runtime mounts.
- Preserve managed UID/GID mappings and host keys across restart and recovery.
  Do not follow user-controlled symlinks when applying privileged filesystem changes.
- A successful backup must contain its required recovery material; a successful
  restore must preserve required ownership, permissions, data, and identity.

## Commands Agents Should Use

Install for development:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
```

Final validation after completing code changes (full ordinary suite, including
all supported Python versions; excludes opt-in release validation):

```bash
tox
```

Available focused checks, not a checklist to run after each change. Select only
the environments and test cases relevant to the affected behavior; prefer direct
pytest in the existing Python 3.14 environment when tox adds no useful validation:

```bash
tox -e lint
tox -e mypy
tox -e py314 -- tests/cli/
tox -e coverage
tox -e docs
tox -e package
tox -e audit
```

Useful direct checks while iterating (use a Python 3.14 project environment and
select the affected test files or cases):

```bash
python -m pytest tests/cli/
sftpwarden validate --config examples/yaml/sftpwarden.yaml
sftpwarden compose --config examples/yaml/sftpwarden.yaml
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
```

For release validation, see the prerequisites, subsets, and cleanup contract in
[tests/release_validation/README.md](tests/release_validation/README.md):

```bash
python -m pytest tests/release_validation --run-release-validation -vv --tb=short
```

`tox -e clean` is cleanup, not validation: it removes generated artifacts and
designated Docker smoke-test images. Inspect its scope before use; do not use it
as a routine test step. If a cloud-synced `.tox` directory interferes, use a fresh
local workdir with `tox --workdir /tmp/sftpwarden-tox`.

## Testing Expectations

- Add focused tests for every behavior change.
- For bug fixes, reproduce the defect before implementing the correction and
  verify the resulting behavior, including relevant failure/recovery cases.
- Keep coverage meaningful. Do not add opaque tests only to satisfy coverage.
- Relevance is mandatory for intermediate validation, including tox: each run
  must address changed behavior, an affected integration or a concrete failure
  hypothesis. Do not run unrelated tox environments merely because they are
  available. Run docs, package, audit or full coverage checks during development
  only when the change affects those areas or a specific risk requires them;
  otherwise leave them for the final validation pass.
- Work efficiently: during implementation, use Python 3.14 and run the smallest
  meaningful test selection covering the changed behavior, its callers and
  relevant failure/recovery paths. Select lint, typing, coverage and external
  checks according to the affected area; do not run every check after each edit.
- Batch related edits into a coherent change before testing. Do not use a test
  run as an automatic response to every edit, progress update or intermediate
  commit. Run sooner when reproducing a defect or checking a risky assumption
  will guide the implementation; defer redundant checks until the final pass.
- Reuse passing results while the relevant code, dependencies, configuration and
  environment remain unchanged. Repeat or broaden checks only when new changes,
  failures or unresolved risks invalidate that evidence. Do not rerun the same
  tests through both direct pytest and tox without a concrete reason.
- Validate fixes locally before pushing. For CI performance changes, measure both
  elapsed time and total execution cost; parallel jobs alone do not prove savings.
  Preserve required coverage and report platform checks that still need CI.
- Run the full ordinary `tox` suite once when the complete code change or agreed
  implementation plan is ready, not after every intermediate step or local
  commit. This final pass covers all supported Python versions and regression
  checks. If corrections follow, rerun the affected checks; repeat the full suite
  only when the scope of those corrections warrants it.
- Run applicable external release gates for the completed work as well; `tox`
  alone does not establish release readiness. Reuse valid external results rather
  than repeating expensive environments without relevant changes.
- Use `tox -e docs` when changes affect the Sphinx documentation or its inputs.
  For standalone Markdown changes outside that build, check the diff, links and
  structure only; do not run unrelated code tests or rebuild unaffected docs.
- For runtime, authentication, deployment, watcher, or recovery changes, verify
  the affected behavior in disposable real environments. An image build, rendered
  manifest, or mocked command alone does not prove SFTP access, persistence,
  process supervision, or a usable restore.
- Keep fast tests independent of infrastructure. Use deterministic clocks and
  concurrency barriers where appropriate, and bounded polling for external tests.
- Report checks actually run and any unverified scope. Missing infrastructure or
  skipped tests do not count as passing required release gates.
- Replace obsolete test expectations with corrected contract assertions. Explain
  removed coverage; do not delete meaningful tests or hide failures with broad
  skips or expected-failure markers.
- Preserve the 95% coverage minimum in `pyproject.toml`; do not lower it. Maintain
  coverage above 97% with meaningful behavior and failure-path tests.

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

- Never expose real secrets, private keys, tokens, customer data, or sensitive
  DSNs in logs, errors, generated public output, commits, or test artifacts.
  Redact sensitive values and use clearly synthetic examples.
- Tests may generate ephemeral credentials and private keys in isolated temporary
  storage with restrictive permissions. Do not use production credentials;
  remove test material afterwards and never include it in diagnostic artifacts.
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
- Keep release-specific findings, tasks, and acceptance matrices in the active
  plan supplied for the task. Keep this guide focused on lasting project rules;
  do not treat planned behavior as already implemented.
- CLI behavior changes require updates to `docs/cli-reference.md` when user-facing.
- Config changes require docs and examples.
- Keep [configuration effects](docs/configuration-effects.md) and the
  [recovery runbook](docs/recovery.md) aligned with implemented behavior.
- Release promotion must use the validated candidate artifacts; do not rebuild
  or overwrite conflicting versioned artifacts during publication.
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

- Prioritize efficient execution: inspect the affected area, make focused changes
  and validate at meaningful checkpoints. Avoid redundant exploration, builds
  and test runs; keep enough evidence to explain what was checked and why.
- Read the relevant code before editing.
- Prefer the existing architecture over new abstractions.
- Make small, reviewable changes with tests.
- Keep public behavior stable unless the task explicitly asks to change it.
- Use dry-run or mocked runners for checks that would otherwise mutate real
  operator environments. Use disposable isolated targets for tests that require
  actual effects; inspect the selected Docker/Kubernetes context first.
- Register cleanup before allocating test resources. Remove only resources owned
  by the test, restore pre-existing image tags when reused, and report incomplete
  cleanup. Never use broad pruning to clean up a test run.
- Remove anonymous volumes with disposable test containers and verify that no
  test-owned storage remains. Do not remove unrelated named volumes or images.
- If changing imports or moving modules, run lint and targeted Python 3.14 tests;
  verify coverage at the appropriate checkpoint or final validation without
  duplicating an unchanged successful coverage run.

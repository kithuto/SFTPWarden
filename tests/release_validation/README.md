# SFTPWarden Release Validation Suite

Management-update cases exercise real owning pip, pipx and uv installations in
private temporary environments. Watcher-update cases replace actual Docker and
Linux supervisord processes, retain checkpoints and verify their versions. The
coordinated-upgrade case checks refusal without consent and actual deployment of
every registered context after consent. Native Windows Task Scheduler and macOS
launchd replacement require separate isolated hosts; mocked command ordering is
not evidence that these native lifecycle checks passed.

Local runs can set `SFTPWARDEN_RELEASE_BUILDER` to a disposable Buildx builder.
Test image builds then use that builder and load their outputs. Register ownership
before creating it and remove the builder and its cache after the run. Fixtures
remove their own containers with anonymous volumes and preserve borrowed image
tags; never use global Docker pruning as test cleanup.

The candidate gate installs the built wheel and verifies that imports come from
that installation, then exercises the exact image/chart artifacts recorded in the
candidate inventory. `SFTPWARDEN_TEST_INSTALLED_ARTIFACT=1` enables the import
guard. Runtime and watcher images can be selected using the fixture environment
variables documented in `conftest.py` and the watcher SSH module.
The package gate also installs the candidate sdist in an isolated environment and
exercises local user, provider and recovery workflows through that installation.

The infrastructure-free deployment-preview contract substitutes deterministic
read-only version and cluster probes while exercising the installed CLI. It checks
the reported source version, generated operations and unchanged input files.
Live SSH, Compose and cluster gates verify the actual probes and effects; fictional
contract endpoints must never be contacted. Disposable SSH gateways and SFTP
runtimes use explicit host ports so outage/restart and maintenance preserve their
endpoints and delivery checkpoints. Port-allocation collisions retry only startup;
they do not extend recovery deadlines or suppress unrelated errors.

CI checks bounded load first on each native image. The installed CLI contracts
run once, independently of architecture. Five external groups then run in parallel
on both architectures, alongside the cluster gates, using the exact same recorded
image archives. `tools/run_release_gates.py` assigns every collected case to one
group and rejects unassigned external tests. Adding a new external module requires
assigning it to a group; nothing is silently omitted. Every group remains required
by `Release aggregate`. Reports include the slowest cases so the execution layout
can be adjusted using measurements.
Current Kubernetes/Helm behavior runs on both native architectures; the separate
amd64 legacy-upgrade job runs concurrently, preserving the complete cluster matrix.
Each external or cluster job stops at its first failure; a passing run still
executes its complete assigned group. Failed groups block publication, and their
unexecuted cases are not counted as passing.
Each external CI group has its own Docker daemon. Running groups concurrently
against one local daemon requires coordinated ownership of shared images; do not
let one group's cleanup remove another group's dependencies.

The Ubuntu/Python 3.14 ordinary job also enforces coverage, so CI does not repeat
that full test run in the quality job. Package validation inspects the candidate
build used downstream, avoiding a second distribution build solely for CI checks.
All twelve native OS/Python combinations remain covered.
New commits supersede older in-progress validation on the same development branch
or PR. Production promotion on `main` is never interrupted by this rule.

`release_upgrade` uses the actual published 1.3.0 image, which has an amd64 runtime
only. Native arm64 jobs explicitly exclude that legacy-only subset; they still
run candidate runtime/provider/cluster gates. Do not describe an excluded upgrade
as a passing native arm64 upgrade test.
Legacy cluster upgrades preserve their deployment mode: manifest cases use the
1.3.0 image's renderer and `kubectl apply`; Helm cases use the recorded 1.3.0
chart. Chart resources and CLI manifests have different immutable selectors,
so switching between them is not a version-upgrade test.

`release_systemd` requires a disposable Linux host with an active systemd manager
and passwordless service administration. Set
`SFTPWARDEN_DISPOSABLE_SYSTEMD_RUNNER=1` only on that host. macOS/Windows cannot
establish native systemd behavior. Selecting the gate without its prerequisites
fails; exclude it explicitly when reporting a narrower local run.

The bounded load gate covers 100/1,000 users, three keys per user, 20 concurrent
authenticated transfers and repeated provider outage/recovery cycles. Its fixed
convergence budget is 25 seconds with a 10-second sync interval. It records timing,
environment and process/resource samples. These are reference workloads, not
universal capacity guarantees. All test resources are removed afterwards.

This folder contains opt-in end-to-end tests for major release validation. These
tests run SFTPWarden like a real operator: subprocess CLI calls, isolated
`SFTPWARDEN_HOME`, temporary project roots, real files, real Docker Compose,
real provider databases in Docker, and real Kubernetes/Helm commands.
Assertions should check effects, not just exit codes: generated config files,
provider content, context registry state, watcher metadata and rendered files,
sync targets, deploy plans, Kubernetes/Helm resources, runtime health, and
actual SFTP behavior where applicable.

Normal unit/integration tests cover the fast contract on every run. This suite is
used by the candidate workflow and for explicit local validation. It uses Docker,
databases, clusters,
real subprocesses, and real network ports when the behavior only proves itself
end to end. For example, the Docker Compose runtime test edits several values in
`sftpwarden.yaml` by hand, runs `sftpwarden deploy`, and then verifies that the
generated Compose file and live container reflect the new port, healthcheck, and
restart policy.

`test_mutation_effects_matrix.py` is intentionally data-driven from the
code-registered config and context command lists. When a new project config path
or context field command is added, release validation must define a real value
and assert the persisted file or registry effect.

They are intentionally excluded from normal `pytest` and `tox` runs. Run them
only when explicitly validating a release:

```bash
python -m pytest tests/release_validation --run-release-validation -vv --tb=short
```

Useful subsets:

```bash
python -m pytest tests/release_validation --run-release-validation \
  -m "release_validation and not release_external"

python -m pytest tests/release_validation --run-release-validation \
  -m "release_docker"

python -m pytest tests/release_validation --run-release-validation \
  -m "release_databases"

python -m pytest tests/release_validation --run-release-validation \
  -m "release_kubernetes or release_helm"
```

## External Requirements

On native Linux, the Docker gates create private files owned by runtime users.
The test runner needs passwordless `sudo rm` for cleanup of its own pytest
temporary directories. Cleanup validates each target against the registered test
directory; it never prunes unrelated Docker resources or changes runtime modes.
The existing-target recovery drill also requires passwordless administration of
the candidate Python executable: it first verifies that an unprivileged in-place
restore is refused, then runs only the restore command as an administrator and
checks that the normal operator retains access to project files.

The focused Compose recovery drill is:

```bash
python -m pytest tests/release_validation/test_fresh_recovery.py \
  --run-release-validation -vv --tb=short
```

It captures YAML/CSV/SQLite runtimes during explicit maintenance. Fresh-target
cases damage the original provider before recovering into an empty project.
Existing-target cases change user data, verify the safety archive and progress
journal, and require the runtime to remain stopped after restoration. Both routes
activate the recovered project as a separate action. They verify pinned SSH
identity, numeric UID/GID, `0600` and `000` files, subsequent download/upload, and
the running/stopped maintenance contract. The module builds one disposable
candidate image and removes
owned containers, images, project files and credentials. It does not establish
remote, Kubernetes, external-database or native Windows recovery readiness.

For project/provider restoration against real databases:

```bash
python -m pytest tests/release_validation/test_database_restore.py \
  --run-release-validation -vv --tb=short
```

This checks safety capture, checked replacement and concurrent-edit rejection
with PostgreSQL, MySQL, MariaDB and a MongoDB replica set, including explicitly
selected empty namespaces. Standalone MongoDB must reject bulk restoration without
changing policy. `test_external_runtime_recovery.py` separately exercises runtime
maintenance and fresh SFTP recovery with data, ownership and trusted host identity
against each transactional engine/topology.

- Docker and Docker Compose v2 for runtime and database provider tests.
- `ssh-keygen` and `sftp` for the real SFTP round trip.
- `kubectl` configured against a disposable-capable cluster for Kubernetes tests.
- `helm` configured against that cluster for Helm tests.
- SFTPWarden provider extras installed for real database tests:
  `mysql`, `mariadb`, `postgres`, and `mongodb`.

Install the full development surface before a release run:

```bash
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
```

## Cleanup Contract

Every test uses temporary project directories and an isolated
`SFTPWARDEN_HOME`. External tests register cleanup callbacks to remove generated
Compose resources, Docker containers, Docker images that were absent before the
test, Helm releases, and Kubernetes namespaces.

The Docker runtime test temporarily builds/tags `sftpwarden:local` because that
is what source checkout Compose deployments use. If that tag existed before the
test, the suite retags the original image ID at cleanup. Restoring an alias never
deletes a borrowed candidate image by its ID.
Container cleanup also removes that test container's anonymous volumes. Named
volumes are preserved unless the test explicitly owns and removes them; global
volume pruning is never part of cleanup.

## Adding New Coverage

When adding a user-facing command or behavior:

- Ensure it is registered on the Typer app; `test_cli_surface.py` discovers
  public commands from code automatically.
- Add a happy-path subprocess flow when users are expected to run it directly.
- Add at least one controlled failure assertion with no Python traceback.
- Assert the concrete effect the command promises: file content, JSON state,
  deployment plan, watcher file, provider row/document, or live resource.
- Put infrastructure-dependent behavior behind the appropriate marker:
  `release_docker`, `release_databases`, `release_kubernetes`, or `release_helm`.
- Register cleanup before creating external resources whenever possible.

# SFTPWarden Release Validation Suite

This folder contains opt-in end-to-end tests for major release validation. These
tests run SFTPWarden like a real operator: subprocess CLI calls, isolated
`SFTPWARDEN_HOME`, temporary project roots, real files, real Docker Compose,
real provider databases in Docker, and real Kubernetes/Helm commands.
Assertions should check effects, not just exit codes: generated config files,
provider content, context registry state, watcher metadata and rendered files,
sync targets, deploy plans, Kubernetes/Helm resources, runtime health, and
actual SFTP behavior where applicable.

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
test, the suite retags the original image ID at cleanup.

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

# Contributing, Development, and Testing

This guide is for people who want to run SFTPWarden from source, contribute a
change, build documentation, or validate the project before publishing a release.

For the shorter GitHub contribution guide, see
[CONTRIBUTING.md](https://github.com/kithuto/sftpwarden/blob/dev/CONTRIBUTING.md).

## Branch Model

SFTPWarden uses `dev` as the integration branch and `main` as the protected
production/release branch.

Contributors should not work directly on `dev` or open normal pull requests to
`main`. Instead:

1. Fork the repository.
2. Create your own branch from `dev`.
3. Develop and validate your change in that branch.
4. Open a Pull Request from your branch to `dev`.

```bash
git clone https://github.com/<your-user>/sftpwarden.git
cd sftpwarden
git remote add upstream https://github.com/kithuto/sftpwarden.git
git fetch upstream
git checkout -b dev upstream/dev
git checkout -b fix/my-change
```

After development, push your branch and open:

```text
fix/my-change -> dev
```

The maintainer promotes accepted changes from `dev` to `main` when preparing
production updates or public releases.

## Install from Source

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
git checkout dev
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,watch,mysql,postgres]"
sftpwarden --version
```

Use Python 3.11, 3.12, 3.13, or 3.14. Install the Python versions locally before
running the full `tox` matrix.

## Development Workflow

Create a branch from `dev` before changing files:

```bash
git checkout dev
git pull origin dev
git checkout -b fix/my-change
```

Run the CLI from the editable install:

```bash
sftpwarden doctor
sftpwarden validate --config examples/yaml/sftpwarden.yaml
```

Build the runtime image when changing Docker/runtime behavior:

```bash
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
docker run --rm --entrypoint sh sftpwarden:local -c 'command -v sshd && command -v sftpwarden'
```

Build the watcher image when changing watcher behavior:

```bash
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
docker run --rm --entrypoint sh sftpwarden-watcher:local -c 'command -v sftpwarden'
```

## Testing

Run the full validation:

```bash
tox
```

The default `tox` run covers:

- lint and formatting;
- tests on Python 3.11, 3.12, 3.13, and 3.14;
- coverage once, instead of repeating it for every Python version;
- Sphinx documentation build;
- package build and wheel content check.

Run one part:

```bash
tox -e lint
tox -e py311
tox -e py312
tox -e py313
tox -e py314
tox -e coverage
tox -e docs
tox -e package
```

Run pytest directly while iterating:

```bash
python -m pytest
python -m pytest tests/test_cli.py
```

## Documentation

Build the Sphinx site locally:

```bash
sphinx-build -b html docs docs/_build/html
```

Keep the README focused on adoption. Put deeper explanations in the specific docs
pages:

- [Configuration](configuration.md)
- [Operations](operations.md)
- [Security](security.md)
- [CLI Reference](cli-reference.md)

## Adding or Changing Features

Keep public behavior stable unless the change is intentional and documented:

- CLI command names and option names should remain compatible.
- `sftpwarden.yaml`, provider files, SQL schemas, and context registry formats
  should remain compatible.
- Runtime state should remain readable across upgrades.
- JSON output should stay parseable.

For user/provider changes, add tests that cover both service behavior and CLI
behavior when the CLI output matters.

For remote/deploy/watcher changes, include dry-run coverage so generated SSH,
rsync, Docker, or systemd commands stay reviewable.

## Pull Request Checklist

Before opening a PR:

- target `dev`, not `main`;
- for docs-only changes, run `tox -e docs`;
- for code changes, run `tox`;
- for Docker/runtime changes, build the affected Docker image locally;
- update README/docs when adoption or operations behavior changes;
- update examples when configuration changes;
- redact secrets from logs and screenshots;
- describe the user-facing behavior, validation performed, and any known limits.

## Release Readiness

Before a public release:

```bash
tox
python -m build
docker build -t sftpwarden:local -f docker/runtime/Dockerfile .
docker build -t sftpwarden-watcher:local -f docker/watcher/Dockerfile .
sftpwarden validate --config examples/yaml/sftpwarden.yaml
sftpwarden validate --config examples/csv/sftpwarden.yaml
SFTPWARDEN_MYSQL_DSN=mysql://user:pass@localhost/sftp \
  sftpwarden validate --config examples/mysql/sftpwarden.yaml
SFTPWARDEN_POSTGRES_DSN=postgresql://user:pass@localhost/sftp \
  sftpwarden validate --config examples/postgres/sftpwarden.yaml
```

Also check the repository-level security workflow before making the repository
public: dependency audit, container scan, SBOM generation, and OpenSSF Scorecard.

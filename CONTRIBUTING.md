# Contributing to SFTPWarden

Thanks for helping improve SFTPWarden. Bug reports, docs fixes, examples, tests,
security hardening, provider improvements and operational feedback are all welcome.

SFTPWarden is infrastructure software, so small, clear, well-tested changes are
preferred over large rewrites.

## Ways to Contribute

Found a bug? Open an issue with:

- what you expected to happen;
- what happened instead;
- your OS, Python version, Docker version and SFTPWarden version or commit;
- the exact command you ran;
- relevant logs or traceback with secrets redacted.

Have an idea? Open an issue describing the operational problem first. A short
example of the workflow you want is more helpful than a large design document.

Want to send code? Open a pull request to `dev`. For larger changes, start with an
issue so we can align on behavior before implementation.

## Branch Workflow

`dev` is the integration branch for contributions. `main` is protected and used
for production and release promotion by the maintainer.

Contributors should work like this:

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

After developing and pushing your branch, open the PR as:

```text
fix/my-change -> dev
```

Normal contribution PRs should not target `main`. The maintainer promotes accepted
changes from `dev` to `main` when preparing production or release updates.

## Development Setup

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
git checkout dev
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,mysql,postgres,mongodb]"
sftpwarden --version
```

The `mysql` extra also enables MariaDB because both providers use PyMySQL. The
`mariadb` extra is an alias for the same dependency.

Use Python 3.11, 3.12, 3.13 or 3.14. The full test matrix uses all four through
`tox`.

## Daily Development Commands

When the complete code change is ready, run the full ordinary validation:

```bash
tox
```

During development, select only the environment relevant to the change:

```bash
tox -e py311
tox -e py312
tox -e py313
tox -e py314
tox -e lint
tox -e mypy
tox -e coverage
tox -e docs
tox -e package
tox -e audit
```

Batch related changes and use Python 3.14 for focused checks while iterating:

```bash
python -m pytest tests/cli/
```

Select tests for the changed behavior and failure/recovery paths. Run the full
ordinary `tox` matrix once the complete change is ready, including documentation.
If corrections follow, repeat the affected checks rather than an unchanged full
matrix. Runtime, deployment, watcher and recovery changes also require disposable
real-environment checks in [release validation](tests/release_validation/README.md).
Mocked tests and rendered manifests do not establish working SFTP or recovery.
Validate corrections locally before pushing another candidate. When changing CI
execution, compare measured elapsed time and total execution cost, preserve every
required gate and distinguish local evidence from checks requiring native hosts.

The ordinary coverage gate requires at least 95% line coverage, with a project
target above 97%. Add meaningful behavior and failure-path tests; do not
exclude executable code to meet the gate.

The release workflow runs on pushes and pull requests to `dev` and `main`. It
builds candidate packages, chart and native images, records their hashes and
validates those artifacts before publication. Configure branch protection to
require the `Release aggregate` check before promotion. Failed or missing gates
block publication. Versioned artifact conflicts are errors, not permission
to overwrite an existing release. Publishing is restricted to the gated `main`
release path; contributor PRs target `dev`. Never include local planning files,
temporary credentials, backup archives or unredacted diagnostics in commits.

The security workflow scans each container image once and retains findings of
every severity in JSON and SARIF reports. A separate check blocks HIGH/CRITICAL
findings, including those without a fix; missing or unreadable reports also fail.
Lower-severity findings remain visible for remediation. Do not suppress an
advisory to make CI pass: update the affected dependency when a fix is available.

The deployment release group rehearses image, multi-architecture index and Helm
chart publication against a disposable loopback registry. It verifies readback,
safe retries and refusal to overwrite conflicting artifacts. Actual publishing
still requires the repository's GHCR permissions, the PyPI trusted publisher
for `release.yml` with environment `pypi`, and Pages access for `main`.
If publication fails partway through, rerun only the failed jobs in the same
workflow run to reuse its verified candidate artifacts. A new run rebuilds the
candidates and may correctly refuse to replace already published bytes.

Build docs:

```bash
sphinx-build -b html docs docs/_build/html
```

Build package artifacts:

```bash
python -m build
```

Optional cleanup after inspecting its scope (not a validation step):

```bash
tox -e clean
```

This removes Python caches, coverage output, docs output, package artifacts and
Docker smoke-test images created during local validation.

## Project Structure

```text
sftpwarden/
  cli_commands/        # Typer command modules
  config/              # Project and global config models
  contexts/            # Context registry and resolution
  providers/           # YAML, CSV, SQLite, SQL and MongoDB providers
  refresh/             # Runtime refresh orchestration
  remote/              # SSH, rsync/scp and remote deploy support
  render/              # Compose and Kubernetes rendering
  security/            # Password/hash helpers
  services/            # Deploy, backup, health and CLI-facing workflows
  system/              # Subprocess wrapper
  users/               # User models and provider mutation helpers
  utils/               # Small shared utilities
  watcher/             # Remote local-sync watcher planning and execution

docker/
  runtime/             # OpenSSH runtime image
  watcher/             # Optional remote local-sync watcher image

charts/                # Official Helm chart
docs/                  # Sphinx documentation
examples/              # Small runnable configuration examples
tests/                 # Unit and CLI tests
tools/                 # Local maintenance helpers
```

## Coding Guidelines

- Keep code, CLI output, logs, examples and docs in English.
- Preserve the public CLI unless a change is explicitly planned.
- Prefer small modules with clear ownership.
- Keep runtime dependencies minimal.
- Use type hints for public functions.
- Use NumPy-style docstrings for public classes and functions.
- Do not print secrets.
- Do not commit generated output such as `.tox/`, `dist/` or `docs/_build/`.
- Preserve unrelated user changes when working in a dirty tree.

## Documentation Guidelines

The README should stay focused on adoption: what the project does, when to use it,
how to install it and the shortest safe path to a running deployment.

Use clear English and avoid serial commas before `and` or `or`. Start with the
simplest working procedure, explain how to verify it and link to advanced detail.
The current documentation targets 1.4.0; basic installation follows the latest
release without a version pin or required virtual environment.

Use `docs/` for detail:

- configuration reference;
- operations and remote deployment workflows;
- security model and limitations;
- CLI reference;
- contribution, development and testing workflow.

## Pull Request Checklist

Before opening a PR:

- target `dev`, not `main`;
- for docs-only changes, run `tox -e docs`;
- for code changes, run `tox`;
- verify runtime or deployment changes in disposable real environments;
- update docs and examples when behavior changes;
- add or update tests for code changes;
- keep secrets out of logs, screenshots and fixtures;
- explain the user-facing impact in the PR description.

## Upgrade Changes

When changing upgrades, keep `sftpwarden/release_compatibility.json` aligned with
the release and tested runtime bounds. Verify owning-manager updates in disposable
pip, pipx and uv installations, and watcher replacement against real supported
runtimes. Preserve checkpoints and prove that compatible management updates do
not deploy servers. Incompatible updates must require explicit all-context consent.
Scheduler command tests do not establish native platform support: identify any
unavailable Windows or macOS scheduler checks separately from passing Linux tests.

## Security Issues

Do not open a public issue for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contribution is licensed under the MIT
License.

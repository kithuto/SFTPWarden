# Contributing to SFTPWarden

Thanks for helping improve SFTPWarden. Bug reports, docs fixes, examples, tests,
security hardening, provider improvements, and operational feedback are all welcome.

SFTPWarden is infrastructure software, so small, clear, well-tested changes are
preferred over large rewrites.

## Ways to Contribute

Found a bug? Open an issue with:

- what you expected to happen;
- what happened instead;
- your OS, Python version, Docker version, and SFTPWarden version or commit;
- the exact command you ran;
- relevant logs or traceback with secrets redacted.

Have an idea? Open an issue describing the operational problem first. A short
example of the workflow you want is more helpful than a large design document.

Want to send code? Open a pull request. For larger changes, start with an issue so
we can align on behavior before implementation.

## Development Setup

```bash
git clone https://github.com/kithuto/sftpwarden.git
cd sftpwarden
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,watch,mysql,postgres]"
sftpwarden --version
```

Use Python 3.11, 3.12, or 3.13. The full test matrix uses all three through `tox`.

## Daily Development Commands

Run the full local validation:

```bash
tox
```

Run one environment:

```bash
tox -e py311
tox -e py312
tox -e py313
tox -e lint
tox -e docs
tox -e package
```

Run pytest directly while iterating:

```bash
python -m pytest
python -m pytest tests/test_cli.py
```

Build docs:

```bash
sphinx-build -b html docs docs/_build/html
```

Build package artifacts:

```bash
python -m build
```

## Project Structure

```text
sftpwarden/
  cli_commands/        # Typer command modules
  config/              # Project and global config models
  providers/           # YAML, CSV, MySQL, PostgreSQL providers
  remote/              # SSH, rsync, deploy planning
  render/              # Docker Compose rendering
  security/            # Password/hash helpers
  services/            # CLI-facing business workflows
  system/              # Subprocess wrapper
  users/               # User models and provider mutation helpers
  utils/               # Small shared utilities

docker/
  runtime/             # OpenSSH runtime image
  watcher/             # Optional remote local-sync watcher image

docs/                  # Sphinx documentation
examples/              # Small runnable configuration examples
tests/                 # Unit and CLI tests
```

## Coding Guidelines

- Keep code, CLI output, logs, examples, and docs in English.
- Preserve the public CLI unless a change is explicitly planned.
- Prefer small modules with clear ownership.
- Keep runtime dependencies minimal.
- Use type hints for public functions.
- Use NumPy-style docstrings for public classes and functions.
- Do not print secrets.
- Do not commit generated output such as `.tox/`, `dist/`, or `docs/_build/`.
- Preserve unrelated user changes when working in a dirty tree.

## Documentation Guidelines

The README should stay focused on adoption: what the project does, when to use it,
how to install it, and the shortest safe path to a running deployment.

Use `docs/` for detail:

- configuration reference;
- operations and remote deployment workflows;
- security model and limitations;
- CLI reference;
- contribution, development, and testing workflow.

## Pull Request Checklist

Before opening a PR:

- run `tox`;
- update docs and examples when behavior changes;
- add or update tests for code changes;
- keep secrets out of logs, screenshots, and fixtures;
- explain the user-facing impact in the PR description.

## Security Issues

Do not open a public issue for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contribution is licensed under the Apache
License 2.0.
